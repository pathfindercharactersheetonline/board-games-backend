from fastapi import FastAPI, Depends, HTTPException, Query, Header
from sqlalchemy.orm import Session
from datetime import datetime
from typing import List
import httpx
import models, schemas
from database import engine, get_db
from urllib.parse import urlencode
from fastapi.middleware.cors import CORSMiddleware
from fastapi import Body
import os
from dotenv import load_dotenv
from datetime import datetime, timedelta # Добавляем timedelta
from fastapi.responses import RedirectResponse

load_dotenv()

# --- КОНФИГУРАЦИЯ ---
YANDEX_CLIENT_ID = os.getenv("YANDEX_CLIENT_ID")
YANDEX_CLIENT_SECRET = os.getenv("YANDEX_CLIENT_SECRET")
BACKEND_URL = os.getenv("BACKEND_URL", "http://127.0.0.1:8000")
YANDEX_REDIRECT_URI = f"{BACKEND_URL}/api/v1/auth/yandex/callback"
# Адрес фронтенда для редиректа после логина
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173")
# API Яндекса (с дефолтными значениями)
YANDEX_AUTH_URL = os.getenv("YANDEX_AUTH_URL", "https://oauth.yandex.ru/authorize")
YANDEX_TOKEN_URL = os.getenv("YANDEX_TOKEN_URL", "https://oauth.yandex.ru/token")
YANDEX_INFO_URL = os.getenv("YANDEX_INFO_URL", "https://login.yandex.ru/info")


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
    base_url = YANDEX_AUTH_URL
    return RedirectResponse(f"{base_url}?{urlencode(params)}")

@app.get("/api/v1/auth/yandex/callback", tags=["Auth"])
async def yandex_callback(code: str, db: Session = Depends(get_db)):
    async with httpx.AsyncClient() as client:
        # 1. Получаем токен
        token_url = YANDEX_TOKEN_URL
        payload = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": YANDEX_CLIENT_ID,
            "client_secret": YANDEX_CLIENT_SECRET,
        }
        
        token_resp = await client.post(token_url, data=payload)
        if token_resp.status_code != 200:
            print(f"Ошибка получения токена: {token_resp.text}")
            raise HTTPException(status_code=400, detail="Ошибка обмена кода на токен")
        
        access_token = token_resp.json().get("access_token")

        # 2. Получаем данные профиля (ИСПРАВЛЕННЫЙ URL)
        user_info_url = YANDEX_INFO_URL
        user_info_resp = await client.get(
            user_info_url,
            headers={"Authorization": f"OAuth {access_token}"}
        )
        
        if user_info_resp.status_code != 200:
            print(f"Ошибка данных профиля: {user_info_resp.text}")
            raise HTTPException(status_code=400, detail="Яндекс отклонил запрос данных профиля")
            
        data = user_info_resp.json()
        email = data.get("default_email") or data.get("emails")[0] if data.get("emails") else None
        yandex_id = str(data.get("id"))

        if not email:
            raise HTTPException(status_code=400, detail="Яндекс не предоставил email")

        # 3. Логика БД в блоке try, чтобы не гадать об ошибках
        try:
            # Ищем по паре провайдер + id
            user = db.query(models.User).filter(
                models.User.auth_provider == "yandex",
                models.User.provider_user_id == yandex_id
            ).first()

            if not user:
                # Проверка по email (на случай если зашел впервые, но email уже в базе)
                user = db.query(models.User).filter(models.User.email == email).first()
                
                if not user:
                    user = models.User(
                        email=email, 
                        role="игрок",
                        auth_provider="yandex",
                        provider_user_id=yandex_id
                    )
                    db.add(user)
                else:
                    # Привязываем данные Яндекса к существующему юзеру
                    user.auth_provider = "yandex"
                    user.provider_user_id = yandex_id
                
                db.commit()
                db.refresh(user)
        except Exception as e:
            db.rollback()
            print(f"Database sync error: {e}")
            raise HTTPException(status_code=500, detail="Ошибка сохранения пользователя в базе")

        # 4. Редирект на фронтенд (добавляем provider для консистентности фронта)
        return RedirectResponse(
            f"{FRONTEND_URL}/?id={user.id}&email={user.email}&role={user.role}&provider=yandex"
        )



# --- GAMES ---

@app.patch("/api/v1/games/{game_id}", response_model=schemas.Game)
def update_game(
    game_id: int, 
    game_update: schemas.GameCreate, 
    db: Session = Depends(get_db), 
    x_user_id: str = Header(None)
):
    # 1. Получаем игру из базы
    db_game = db.query(models.Game).filter(models.Game.id == game_id).first()
    if not db_game:
        raise HTTPException(status_code=404, detail="Игра не найдена")

    # 2. Получаем текущего пользователя для проверки прав
    current_user = db.query(models.User).filter(models.User.id == int(x_user_id)).first()
    if not current_user:
        raise HTTPException(status_code=401, detail="Пользователь не авторизован")

    # 3. ПРОВЕРКА ПРАВ:
    # Разрешаем, если пользователь — админ ИЛИ если он мастер этой конкретной игры
    is_admin = current_user.role == "администратор"
    is_master_of_game = db_game.master_name == current_user.email

    if not (is_admin or is_master_of_game):
        raise HTTPException(
            status_code=403, 
            detail="У вас недостаточно прав для редактирования этой игры"
        )

    # 4. Обновление полей
    for key, value in game_update.dict().items():
        setattr(db_game, key, value)

    db.commit()
    db.refresh(db_game)
    return db_game


@app.get("/api/v1/games/{game_id}", response_model=schemas.Game, tags=["Games"])
def get_game(game_id: int, db: Session = Depends(get_db)):
    db_game = db.query(models.Game).filter(models.Game.id == game_id).first()
    if not db_game:
        raise HTTPException(status_code=404, detail="Игра не найдена")
    
    # Добавляем расчет текущего кол-ва игроков для схемы
    db_game.current_players = len(db_game.booked_users)
    return db_game

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
    try:
        # 1. Ищем игру в базе
        db_game = db.query(models.Game).filter(models.Game.id == game_id).first()
        if not db_game:
            raise HTTPException(status_code=404, detail="Игра не найдена")
        
        # 2. Проверка полномочий: Админ может всё, Мастер — только своё
        # (Проверьте, что в модели Game поле называется master_id)
        is_admin = current_user.role == "администратор"
        is_owner = hasattr(db_game, 'master_id') and db_game.master_id == current_user.id
        
        if not (is_admin or is_owner):
            raise HTTPException(
                status_code=403, 
                detail="У вас недостаточно прав для удаления этой игры"
            )

        # 3. УДАЛЯЕМ СВЯЗАННЫЕ ЗАПИСИ (Bookings)
        # Это предотвратит ошибку 500 (Foreign Key Constraint)
        db.query(models.Booking).filter(models.Booking.game_id == game_id).delete()
        
        # 4. Удаляем саму игру
        db.delete(db_game)
        db.commit()
        
        return {"status": "success", "message": "Игра и записи удалены"}

    except Exception as e:
        db.rollback()
        print(f"Ошибка при удалении игры {game_id}: {str(e)}")
        raise HTTPException(
            status_code=500, 
            detail=f"Внутренняя ошибка сервера при удалении: {str(e)}"
        )


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

@app.delete("/api/v1/bookings/leave/{game_id}", tags=["Bookings"])
def leave_game(game_id: int, db: Session = Depends(get_db), x_user_id: str = Header(None)):
    user_id = int(x_user_id)
    booking = db.query(models.Booking).filter(
        models.Booking.game_id == game_id, 
        models.Booking.user_id == user_id
    ).first()
    
    if not booking:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    
    db.delete(booking)
    db.commit()
    return {"message": "Вы успешно отписались"}


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
def cleanup_old_games(
    days: int = Query(30, ge=0, description="Удалить игры старше этого количества дней"),
    db: Session = Depends(get_db), 
    admin: models.User = Depends(get_admin_only)
):
    """
    Удаляет игры, которые прошли более чем N дней назад.
    По умолчанию: удаляет игры старше 30 дней.
    Если передать days=0, удалятся все прошедшие игры.
    """
    # Вычисляем пороговую дату (сегодня - N дней)
    threshold_date = datetime.now() - timedelta(days=days)
    
    # Фильтруем игры, которые были проведены ДО этой даты
    query = db.query(models.Game).filter(models.Game.date_time < threshold_date)
    deleted = query.count()
    
    query.delete(synchronize_session=False)
    db.commit()
    
    return {
        "status": "success",
        "message": f"Очистка завершена",
        "deleted_count": deleted,
        "threshold_date": threshold_date.strftime("%Y-%m-%d %H:%M")
    }
