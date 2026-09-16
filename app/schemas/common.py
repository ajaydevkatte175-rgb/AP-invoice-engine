from pydantic import BaseModel, Field


class PaginationParams(BaseModel):
    page: int = Field(default=1, ge=1, description="Page number, 1-indexed")
    page_size: int = Field(default=50, ge=1, le=200, description="Items per page")

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


class PaginatedResponse[T](BaseModel):
    items: list[T]
    total: int
    page: int
    page_size: int
    total_pages: int


class MessageResponse(BaseModel):
    message: str
    detail: str | None = None
