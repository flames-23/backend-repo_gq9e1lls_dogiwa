import os
from typing import List, Optional, Literal, Any, Dict
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from bson import ObjectId

from database import db, create_document
from schemas import Vendor, Payment
from pymongo import GEOSPHERE

app = FastAPI(title="Madad MVP API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Ensure geospatial index on vendor.location
if db is not None:
    try:
        db["vendor"].create_index([("location", GEOSPHERE)])
    except Exception:
        pass


class UpdateVendor(BaseModel):
    approved: Optional[bool] = None
    verified: Optional[bool] = None
    payment_status: Optional[Literal["unpaid", "active", "expired"]] = None
    name: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    description: Optional[str] = None


def serialize_doc(doc: Dict[str, Any]) -> Dict[str, Any]:
    if not doc:
        return doc
    doc = dict(doc)
    _id = doc.get("_id")
    if isinstance(_id, ObjectId):
        doc["id"] = str(_id)
        del doc["_id"]
    # Ensure any nested object ids are converted if needed later
    return doc


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


# Vendors
@app.post("/api/vendors")
def create_vendor(vendor: Vendor):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not available")
    # Insert and return created
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
def update_vendor(vendor_id: str, payload: UpdateVendor):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not available")
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


# Simple admin list views (no auth for MVP preview)
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


# Payment record creation (manual confirmation in MVP)
@app.post("/api/payments")
def create_payment(payment: Payment):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not available")
    pid = create_document("payment", payment)
    doc = db["payment"].find_one({"_id": ObjectId(pid)})
    return serialize_doc(doc)


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
