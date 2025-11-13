"""
Database Schemas

Madad MVP collections using MongoDB with Pydantic models for validation.
Each Pydantic model name maps to a MongoDB collection with the lowercase name.

Collections:
- Vendor: registered service providers with geolocation (GeoJSON Point)
- Payment: records of vendor subscription payments (MVP: manual status tracking)
"""

from pydantic import BaseModel, Field, EmailStr
from typing import Optional, Literal, List, Dict, Any

ServiceType = Literal[
    "tow_truck",
    "mechanic",
    "hotel",
    "medical",
    "car_wash",
    "electrician",
    "plumber",
]

class GeoPoint(BaseModel):
    type: Literal["Point"] = "Point"
    # GeoJSON expects [longitude, latitude]
    coordinates: List[float] = Field(..., min_items=2, max_items=2, description="[lng, lat]")

class Vendor(BaseModel):
    name: str = Field(..., description="Vendor display name")
    phone: str = Field(..., description="Primary contact phone number in local format")
    service_type: ServiceType = Field(..., description="Primary service category")
    location: GeoPoint = Field(..., description="GeoJSON point for the vendor location")
    address: Optional[str] = Field(None, description="Text address or area name")
    description: Optional[str] = Field(None, description="Short description or specialties")
    approved: bool = Field(False, description="Admin approval status")
    verified: bool = Field(False, description="Manual vendor verification status")
    # MVP payments: manual approval; future: integrate Easypaisa/JazzCash
    payment_status: Literal["unpaid", "active", "expired"] = Field("unpaid")

class Payment(BaseModel):
    vendor_id: str = Field(..., description="Reference to vendor _id as string")
    amount_pkr: int = Field(..., ge=0)
    method: Literal["easypaisa", "jazzcash", "manual"] = "manual"
    status: Literal["pending", "confirmed", "failed"] = "pending"
    reference: Optional[str] = None
    notes: Optional[str] = None

# Optional: Simple auth identities for future extension
class UserIdentity(BaseModel):
    provider: Literal["guest", "google", "phone"] = "guest"
    uid: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[EmailStr] = None
