from fastapi import APIRouter

from app.api.v1.customers import router as customers_router
from app.api.v1.invoices import router as invoices_router
from app.api.v1.issued_invoices import router as issued_invoices_router
from app.api.v1.vendors import router as vendors_router

api_v1_router = APIRouter(prefix="/api/v1")

api_v1_router.include_router(vendors_router)
api_v1_router.include_router(invoices_router)
api_v1_router.include_router(customers_router)
api_v1_router.include_router(issued_invoices_router)

__all__ = ["api_v1_router"]
