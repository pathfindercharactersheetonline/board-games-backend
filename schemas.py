from pydantic import BaseModel, Field
from datetime import datetime
from typing import List, Optional

# --- СХЕМЫ ПОЛЬЗОВАТЕЛЕЙ ---

class UserBase(BaseModel):
    email: str
    role: str = "игрок"
    # Добавляем поле, чтобы знать, через какой сервис зашел пользователь
    auth_provider: Optional[str] = None 

class UserShort(BaseModel):
    id: int
    email: str
    role: str
    class Config:
        from_attributes = True

class User(UserBase):
    id: int
    # Добавляем ID провайдера, чтобы он был доступен в полной модели пользователя
    provider_user_id: Optional[str] = None 
    class Config:
        from_attributes = True


# --- СХЕМЫ ИГР ---

class GameBase(BaseModel):
    title: str = Field(..., example="D&D: Шахта Фанделвера")
    master_name: str = Field(..., example="Алексей С.")
    image_url: str = Field(..., example="https://example.com/image.jpg")
    description: str = Field(..., example="Описание в формате Markdown")
    max_players: int = Field(..., gt=0, example=6)
    date_time: datetime

class GameCreate(GameBase):
    pass

class Game(GameBase):
    id: int
    current_players: int = 0
    booked_users: List[UserShort] = [] # Ссылается на UserShort, объявленный выше

    class Config:
        from_attributes = True

# --- СХЕМЫ ЗАПИСЕЙ ---

class BookingCreate(BaseModel):
    game_id: int

class StatusResponse(BaseModel):
    status: str
    message: str
