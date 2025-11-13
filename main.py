import os
from datetime import datetime, timedelta, timezone
from typing import Optional, Literal, Any, Dict
from fastapi import FastAPI, HTTPException, Query, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr
from bson import ObjectId

from database import db, create_document
from schemas import Vendor, Payment, User, UserOut, LoginRequest
from pymongo import GEOSPHERE, ASCENDING
from passlib.context import CryptContext
import jwt

app = FastAPI(title="Madad MVP API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Config
JWT_SECRET = os.getenv("JWT_SECRET", "dev_secret_change_me")
JWT_ALGO = os.getenv("JWT_ALGO", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "43200"))  # 30 days by default
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Indexes
if db is not None:
    try:
        db["vendor"].create_index([("location", GEOSPHERE)])
        db["user"].create_index([("email", ASCENDING)], unique=True, sparse=True)
        db["user"].create_index([("phone", ASCENDING)], unique=True, sparse=True)
    except Exception:
        pass


def serialize_doc(doc: Dict[str, Any]) -> Dict[str, Any]:
    if not doc:
        return doc
    doc = dict(doc)
    _id = doc.get("_id")
    if isinstance(_id, ObjectId):
        doc["id"] = str(_id)
        del doc["_id"]
    return doc


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    return pwd_context.verify(password, hashed)


def create_access_token(sub: str) -> str:
    now = datetime.now(timezone.utc)
    exp = now + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": sub, "exp": exp}
    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)
    return token


class UpdateVendor(BaseModel):
    approved: Optional[bool] = None
    verified: Optional[bool] = None
    payment_status: Optional[Literal["unpaid", "active", "expired"]] = None
    name: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    description: Optional[str] = None


@app.get("/")
def read_root():
    return {"name": "Madad API", "status": "ok"}


@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": "❌ Not Set",
        "database_name": "❌ Not Set",
        "connection_status": "Not Connected",
        "collections": [],
    }
    try:
        if db is not None:
            response["database"] = "✅ Available"
            response["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
            response["database_name"] = os.getenv("DATABASE_NAME") or "❌ Not Set"
            try:
                cols = db.list_collection_names()
                response["collections"] = cols
                response["database"] = "✅ Connected & Working"
                response["connection_status"] = "Connected"
            except Exception as e:
                response["database"] = f"⚠️ Connected but error: {str(e)[:80]}"
        else:
            response["database"] = "⚠️ Database not initialized"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:120]}"
    return response


# ---------- AUTH ----------
class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


def get_current_user(authorization: Optional[str] = Header(None)) -> Optional[Dict[str, Any]]:
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    token = authorization.split(" ", 1)[1].strip()
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
        sub = payload.get("sub")
        if not sub or db is None:
            return None
        doc = db["user"].find_one({"_id": ObjectId(sub)})
        return doc
    except Exception:
        return None


@app.post("/api/auth/register", response_model=TokenResponse)
def register(user: User):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not available")
    # Ensure either email or phone present
    if not user.email and not user.phone:
        raise HTTPException(status_code=400, detail="Email or phone is required")
    # Uniqueness checks
    if user.email and db["user"].find_one({"email": user.email}):
        raise HTTPException(status_code=409, detail="Email already registered")
    if user.phone and db["user"].find_one({"phone": user.phone}):
        raise HTTPException(status_code=409, detail="Phone already registered")

    data = user.model_dump()
    raw_password = data.pop("password")
    data["hashed_password"] = hash_password(raw_password)
    data["created_at"] = datetime.now(timezone.utc)
    data["updated_at"] = datetime.now(timezone.utc)

    res = db["user"].insert_one(data)
    uid = str(res.inserted_id)

    token = create_access_token(uid)
    user_out = UserOut(id=uid, name=data.get("name"), email=data.get("email"), phone=data.get("phone"))
    return {"access_token": token, "token_type": "bearer", "user": user_out}


@app.post("/api/auth/login", response_model=TokenResponse)
def login(req: LoginRequest):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not available")
    if not req.email and not req.phone:
        raise HTTPException(status_code=400, detail="Email or phone is required")

    q: Dict[str, Any] = {"email": req.email} if req.email else {"phone": req.phone}
    doc = db["user"].find_one(q)
    if not doc:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    hashed = doc.get("hashed_password")
    if not hashed or not verify_password(req.password, hashed):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    uid = str(doc["_id"])

    token = create_access_token(uid)
    user_out = UserOut(id=uid, name=doc.get("name"), email=doc.get("email"), phone=doc.get("phone"))
    return {"access_token": token, "token_type": "bearer", "user": user_out}


@app.get("/api/auth/me", response_model=UserOut)
def me(current = Depends(get_current_user)):
    if not current:
        raise HTTPException(status_code=401, detail="Unauthorized")
    doc = serialize_doc(current)
    return UserOut(id=doc["id"], name=doc.get("name"), email=doc.get("email"), phone=doc.get("phone"))


# ---------- VENDORS ----------
@app.post("/api/vendors")
def create_vendor(vendor: Vendor, current = Depends(get_current_user)):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not available")
    if not current:
        raise HTTPException(status_code=401, detail="Unauthorized")
    vid = create_document("vendor", vendor)
    doc = db["vendor"].find_one({"_id": ObjectId(vid)})
    return serialize_doc(doc)


@app.get("/api/vendors/{vendor_id}")
def get_vendor(vendor_id: str):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not available")
    try:
        doc = db["vendor"].find_one({"_id": ObjectId(vendor_id)})
        if not doc:
            raise HTTPException(status_code=404, detail="Vendor not found")
        return serialize_doc(doc)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid vendor id")


@app.patch("/api/vendors/{vendor_id}")
def update_vendor(vendor_id: str, payload: "UpdateVendor", current = Depends(get_current_user)):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not available")
    if not current:
        raise HTTPException(status_code=401, detail="Unauthorized")
    try:
        update = {k: v for k, v in payload.model_dump(exclude_none=True).items()}
        if not update:
            return {"updated": False}
        res = db["vendor"].update_one({"_id": ObjectId(vendor_id)}, {"$set": update})
        if res.matched_count == 0:
            raise HTTPException(status_code=404, detail="Vendor not found")
        doc = db["vendor"].find_one({"_id": ObjectId(vendor_id)})
        return serialize_doc(doc)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid vendor id")


@app.get("/api/vendors/nearby")
def nearby_vendors(
    lng: float = Query(..., description="Longitude"),
    lat: float = Query(..., description="Latitude"),
    radius_km: float = Query(5.0, description="Search radius in kilometers"),
    service_type: Optional[str] = Query(None, description="Filter by service type"),
):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not available")

    query: Dict[str, Any] = {
        "location": {
            "$near": {
                "$geometry": {"type": "Point", "coordinates": [lng, lat]},
                "$maxDistance": int(radius_km * 1000),
            }
        },
        "approved": True,
        "payment_status": "active",
    }

    if service_type:
        query["service_type"] = service_type

    cursor = db["vendor"].find(query).limit(200)
    results = [serialize_doc(doc) for doc in cursor]
    return {"count": len(results), "vendors": results}


# Admin list (left open for now)
@app.get("/api/admin/vendors")
def admin_list_vendors(status: Optional[str] = Query(None, description="pending|active|all")):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not available")
    q: Dict[str, Any] = {}
    if status == "pending":
        q = {"approved": False}
    elif status == "active":
        q = {"approved": True, "payment_status": "active"}
    cursor = db["vendor"].find(q).limit(500)
    return [serialize_doc(d) for d in cursor]


# Payments
@app.post("/api/payments")
def create_payment(payment: Payment, current = Depends(get_current_user)):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not available")
    if not current:
        raise HTTPException(status_code=401, detail="Unauthorized")
    pid = create_document("payment", payment)
    doc = db["payment"].find_one({"_id": ObjectId(pid)})
    return serialize_doc(doc)


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
