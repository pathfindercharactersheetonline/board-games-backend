from fastapi import FastAPI, Depends, HTTPException, Query, status, Header
from sqlalchemy.orm import Session
from datetime import datetime
from typing import List
import httpx

import models, schemas
from database import engine, get_db
from urllib.parse import urlencode

from fastapi import FastAPI, Depends, HTTPException, Query, status, Header
from fastapi.middleware.cors import CORSMiddleware # Импортируем Middleware

import os
from dotenv import load_dotenv
load_dotenv()

# --- КОНФИГУРАЦИЯ YANDEX ---
YANDEX_REDIRECT_URI = "http://127.0.0.1:8000/api/v1/auth/yandex/callback"
YANDEX_CLIENT_ID = os.getenv("YANDEX_CLIENT_ID")
YANDEX_CLIENT_SECRET = os.getenv("YANDEX_CLIENT_SECRET")

# Инициализация БД (создание таблиц)
models.Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Board Games API",
    description="Backend сервис для организации настольных игр",
    version="1.3.0"
)

# --- НАСТРОЙКА CORS ---
# Список разрешенных адресов (откуда приходят запросы)
origins = [
    "http://localhost:5173",    # Твой Vite/React локально
    "http://127.0.0.1:5173",   # Альтернативный адрес фронтенда
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,            # Разрешаем запросы с этих адресов
    allow_credentials=True,           # Разрешаем передачу Cookies/Auth-заголовков
    allow_methods=["*"],              # Разрешаем все методы (GET, POST, PATCH, DELETE)
    allow_headers=["*"],              # Разрешаем любые заголовки (включая наш X-User-Id)
)

# --- DEPENDENCIES (Проверка полномочий) ---

def get_current_user(db: Session = Depends(get_db), x_user_id: int = Header(...)):
    user = db.query(models.User).filter(models.User.id == x_user_id).first()
    if not user:
        raise HTTPException(status_code=401, detail="Пользователь не найден")
    return user

def get_admin_only(user: models.User = Depends(get_current_user)):
    if user.role != "администратор":
        raise HTTPException(status_code=403, detail="Доступ только для администраторов")
    return user

# --- AUTH (YANDEX) ---

@app.get("/api/v1/auth/yandex/login", tags=["Auth"])
def yandex_login():
    # Настройки (убедись, что они совпадают с панелью https://oauth.yandex.ru)
    params = {
        "response_type": "code",
        "client_id": YANDEX_CLIENT_ID,
        "redirect_uri": YANDEX_REDIRECT_URI,
        # Запрашиваем доступ к почте и данным профиля
        "scope": "login:email login:info" 
    }
    
    # ВАЖНО: здесь должен быть путь /authorize
    base_url = "https://oauth.yandex.ru/authorize"
    query_string = urlencode(params)
    
    full_url = f"{base_url}?{query_string}"
    
    return {"url": full_url}

@app.get("/api/v1/auth/yandex/callback", tags=["Auth"])
async def yandex_callback(code: str, db: Session = Depends(get_db)):

    async with httpx.AsyncClient() as client:
        # ШАГ 1: ОБМЕН КОДА НА ТОКЕН
        # Используем полный абсолютный URL Яндекса
        token_url = "https://oauth.yandex.ru/token"
        
        payload = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": YANDEX_CLIENT_ID,
            "client_secret": YANDEX_CLIENT_SECRET,
        }
        
        # Важно: Яндекс ждет данные в формате x-www-form-urlencoded
        token_resp = await client.post(
            token_url, 
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"}
        )

        # Если здесь прилетит "Cannot POST /", значит token_url был неверным.
        # Но с этим кодом мы увидим реальный ответ Яндекса.
        if token_resp.status_code != 200:
            print(f"DEBUG Error: {token_resp.text}")
            raise HTTPException(
                status_code=400, 
                detail=f"Yandex Token Error: {token_resp.text}"
            )

        token_data = token_resp.json()
        access_token = token_data.get("access_token")

        # --- ШАГ 2: ПОЛУЧЕНИЕ ИНФО О ПОЛЬЗОВАТЕЛЕ ---
        access_token = token_data.get("access_token")
        if not access_token:
            print(f"Критическая ошибка: Яндекс не прислал токен! Весь ответ: {token_data}")
            raise HTTPException(status_code=400, detail="Token not found in Yandex response")

        user_info_url = "https://login.yandex.ru/info"
        
        # ВАЖНО: Убедитесь, что между OAuth и токеном ровно ОДИН пробел
        user_info_resp = await client.get(
            user_info_url,
            headers={
                "Authorization": f"OAuth {access_token}",
                "Accept": "application/json"
            },
            params = {
                "format": "json"
            },
            follow_redirects=False
        )
        # 1. Выведет текст ответа, если это не JSON (например, ошибку сервера)
        print(f"Status: {user_info_resp.status_code}")
        print(f"Content: {user_info_resp.text}") 
        # 2. Безопасный парсинг
        if user_info_resp.status_code == 200:
            data = user_info_resp.json()
        else:
            print("Ошибка API!")


        if user_info_resp.status_code != 200:
            print(f"Ошибка получения профиля: {user_info_resp.status_code}")
            print(f"Текст ответа: {user_info_resp.text}")
            raise HTTPException(
                status_code=400, 
                detail=f"User Info Error: {user_info_resp.text[:100]}"
            )
            
        data = user_info_resp.json()

    # --- ШАГ 3: СОХРАНЕНИЕ В БАЗУ ---
    email = data.get("default_email")
    yandex_id = str(data.get("id"))
    full_name = data.get("real_name") or data.get("display_name") or "Новый игрок"

    # Ищем или создаем пользователя
    user = db.query(models.User).filter(models.User.email == email).first()
    if not user:
        user = models.User(email=email, full_name=full_name, role="игрок")
        db.add(user)
        db.commit()
        db.refresh(user)

    # Привязываем Identity
    identity = db.query(models.UserIdentity).filter(
        models.UserIdentity.provider == "yandex",
        models.UserIdentity.provider_user_id == yandex_id
    ).first()

    if not identity:
        db.add(models.UserIdentity(user_id=user.id, provider="yandex", provider_user_id=yandex_id))
        db.commit()

    return {
        "status": "success",
        "user_id": user.id,
        "full_name": user.full_name,
        "role": user.role
    }

# --- USERS ---

@app.patch("/api/v1/users/{user_id}/name", response_model=schemas.User, tags=["Users"])
def update_user_name(
    user_id: int, 
    update_data: schemas.UserUpdate, 
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    target_user = db.query(models.User).filter(models.User.id == user_id).first()
    if not target_user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    # Проверка прав: сам себя или админ
    if current_user.role != "администратор" and current_user.id != user_id:
        raise HTTPException(status_code=403, detail="Нет прав на изменение чужого имени")

    target_user.full_name = update_data.full_name
    db.commit()
    db.refresh(target_user)
    return target_user

# --- GAMES ---

@app.get("/api/v1/games", response_model=List[schemas.Game], tags=["Games"])
def get_games(
    skip: int = Query(0, ge=0), 
    limit: int = Query(10, ge=1, le=100), 
    db: Session = Depends(get_db)
):
    games = db.query(models.Game).offset(skip).limit(limit).all()
    for game in games:
        # Получаем записи через связь Booking -> User
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
    # Проверка роли Мастера/Админа
    if current_user.role not in ["мастер", "администратор"]:
        raise HTTPException(status_code=403, detail="Нужна роль Мастера")
        
    db_game = models.Game(**game.model_dump())
    db.add(db_game)
    db.commit()
    db.refresh(db_game)
    db_game.current_players = 0
    db_game.booked_users = []
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
    
    # Мастер удаляет только свои, админ любые
    is_owner = db_game.master_name == current_user.full_name
    if current_user.role != "администратор" and not is_owner:
        raise HTTPException(status_code=403, detail="Вы можете удалять только свои игры")

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
    if not game: raise HTTPException(status_code=404, detail="Игра не найдена")
    
    count = db.query(models.Booking).filter(models.Booking.game_id == game.id).count()
    if count >= game.max_players: raise HTTPException(status_code=400, detail="Мест нет")
    
    # Проверка на дубль
    existing = db.query(models.Booking).filter(models.Booking.game_id==game.id, models.Booking.user_id==user.id).first()
    if existing: raise HTTPException(status_code=400, detail="Уже записан")

    new_booking = models.Booking(game_id=game.id, user_id=user.id)
    db.add(new_booking)
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
    if not user: raise HTTPException(status_code=404, detail="Пользователь не найден")
    user.role = new_role
    db.commit()
    return {"status": "success"}

@app.delete("/api/v1/admin/cleanup-old-games", tags=["Admin"])
def cleanup_old_games(db: Session = Depends(get_db), admin: models.User = Depends(get_admin_only)):
    now = datetime.now()
    deleted = db.query(models.Game).filter(models.Game.date_time < now).delete()
    db.commit()
    return {"detail": f"Удалено: {deleted}"}
