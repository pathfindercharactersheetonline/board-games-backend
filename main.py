from fastapi import FastAPI, Depends, HTTPException, Query, Header
from sqlalchemy.orm import Session
from datetime import datetime
from typing import List
import httpx
import models, schemas
from database import engine, get_db
from urllib.parse import urlencode
from fastapi.middleware.cors import CORSMiddleware
import os
from dotenv import load_dotenv

load_dotenv()

# --- КОНФИГУРАЦИЯ ---
YANDEX_CLIENT_ID = os.getenv("YANDEX_CLIENT_ID")
YANDEX_CLIENT_SECRET = os.getenv("YANDEX_CLIENT_SECRET")
# Согласно требованию:
YANDEX_REDIRECT_URI = "http://127.0.0.1:8000/api/v1/auth/yandex/callback"

models.Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Board Games API",
    description="Backend сервис (Идентификация по ID, Яндекс OAuth)",
    version="1.6.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- DEPENDENCIES ---
def get_current_user(db: Session = Depends(get_db), x_user_id: int = Header(...)):
    user = db.query(models.User).filter(models.User.id == x_user_id).first()
    if not user:
        raise HTTPException(status_code=401, detail="Пользователь не найден")
    return user

def get_admin_only(user: models.User = Depends(get_current_user)):
    if user.role != "администратор":
        raise HTTPException(status_code=403, detail="Требуются права администратора")
    return user

# --- AUTH (YANDEX) ---
@app.get("/api/v1/auth/yandex/login", tags=["Auth"])
def yandex_login():
    params = {
        "response_type": "code",
        "client_id": YANDEX_CLIENT_ID,
        "redirect_uri": YANDEX_REDIRECT_URI,
        "scope": "login:email"
    }
    base_url = "https://oauth.yandex.ru/authorize"
    return {"url": f"{base_url}?{urlencode(params)}"}

@app.get("/api/v1/auth/yandex/callback", tags=["Auth"])
async def yandex_callback(code: str, db: Session = Depends(get_db)):
    async with httpx.AsyncClient() as client:
        # Согласно требованию: token_url = authorize
        token_url = "https://oauth.yandex.ru/authorize"
        payload = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": YANDEX_CLIENT_ID,
            "client_secret": YANDEX_CLIENT_SECRET,
        }
        
        # Обмен кода на токен
        token_resp = await client.post(token_url, data=payload)
        if token_resp.status_code != 200:
            raise HTTPException(status_code=400, detail="Ошибка авторизации в Яндексе")
        
        token_data = token_resp.json()
        access_token = token_data.get("access_token")

        # Получение данных пользователя
        user_info_resp = await client.get(
            "https://login.yandex.ru",
            headers={"Authorization": f"OAuth {access_token}"}
        )
        
        if user_info_resp.status_code != 200:
            raise HTTPException(status_code=400, detail="Ошибка получения данных профиля")
            
        data = user_info_resp.json()
        email = data.get("default_email")
        yandex_id = str(data.get("id"))

        # Работа с БД только по email/id (без full_name)
        user = db.query(models.User).filter(models.User.email == email).first()
        if not user:
            user = models.User(email=email, role="игрок")
            db.add(user)
            db.commit()
            db.refresh(user)

        identity = db.query(models.UserIdentity).filter(
            models.UserIdentity.provider == "yandex",
            models.UserIdentity.provider_user_id == yandex_id
        ).first()
        
        if not identity:
            db.add(models.UserIdentity(user_id=user.id, provider="yandex", provider_user_id=yandex_id))
            db.commit()

        return {"status": "success", "user_id": user.id, "role": user.role}

# --- GAMES ---
@app.get("/api/v1/games", response_model=List[schemas.Game], tags=["Games"])
def get_games(skip: int = Query(0), limit: int = Query(10), db: Session = Depends(get_db)):
    games = db.query(models.Game).offset(skip).limit(limit).all()
    for game in games:
        bookings = db.query(models.Booking).filter(models.Booking.game_id == game.id).all()
        game.current_players = len(bookings)
        game.booked_users = [b.user for b in bookings]
    return games

@app.post("/api/v1/games", response_model=schemas.Game, tags=["Games"])
def create_game(
    game: schemas.GameCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    if current_user.role not in ["мастер", "администратор"]:
        raise HTTPException(status_code=403, detail="Недостаточно прав для создания игры")
    
    db_game = models.Game(**game.model_dump())
    db_game.master_id = current_user.id 
    db.add(db_game)
    db.commit()
    db.refresh(db_game)
    return db_game

@app.delete("/api/v1/games/{game_id}", tags=["Games"])
def delete_game(
    game_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    db_game = db.query(models.Game).filter(models.Game.id == game_id).first()
    if not db_game:
        raise HTTPException(status_code=404, detail="Игра не найдена")
    
    # Проверка владельца по ID
    if current_user.role != "администратор" and db_game.master_id != current_user.id:
        raise HTTPException(status_code=403, detail="Вы не можете удалить чужую игру")
        
    db.delete(db_game)
    db.commit()
    return {"status": "success"}

# --- BOOKINGS ---
@app.post("/api/v1/bookings/join", tags=["Bookings"])
def join_game(
    booking: schemas.BookingCreate,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user)
):
    game = db.query(models.Game).filter(models.Game.id == booking.game_id).first()
    if not game:
        raise HTTPException(status_code=404, detail="Игра не найдена")
    
    count = db.query(models.Booking).filter(models.Booking.game_id == game.id).count()
    if count >= game.max_players:
        raise HTTPException(status_code=400, detail="Мест нет")
    
    existing = db.query(models.Booking).filter(
        models.Booking.game_id == game.id,
        models.Booking.user_id == user.id
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Вы уже записаны")
    
    db.add(models.Booking(game_id=game.id, user_id=user.id))
    db.commit()
    return {"status": "success"}

# --- ADMIN ---
@app.get("/api/v1/admin/users", response_model=List[schemas.User], tags=["Admin"])
def list_users(db: Session = Depends(get_db), admin: models.User = Depends(get_admin_only)):
    return db.query(models.User).all()

@app.patch("/api/v1/admin/users/{user_id}/role", tags=["Admin"])
def change_role(
    user_id: int,
    new_role: str,
    db: Session = Depends(get_db),
    admin: models.User = Depends(get_admin_only)
):
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    user.role = new_role
    db.commit()
    return {"status": "success"}

@app.delete("/api/v1/admin/cleanup-old-games", tags=["Admin"])
def cleanup_old_games(db: Session = Depends(get_db), admin: models.User = Depends(get_admin_only)):
    deleted = db.query(models.Game).filter(models.Game.date_time < datetime.now()).delete()
    db.commit()
    return {"detail": f"Удалено игр: {deleted}"}
