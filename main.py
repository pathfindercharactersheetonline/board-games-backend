from fastapi import FastAPI, Depends, HTTPException, Query, Header
from sqlalchemy.orm import Session
from typing import List
import httpx
import models, schemas
from database import engine, get_db
from urllib.parse import urlencode
from fastapi.middleware.cors import CORSMiddleware
from fastapi import Body
import os
from dotenv import load_dotenv
from datetime import datetime, timedelta 
from fastapi.responses import RedirectResponse
import jwt
import logging
from models import UserRole 

# Настройка базового конфига
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(), # Вывод в консоль (для Docker)
        logging.FileHandler("app.log") # Запись в файл для истории
    ]
)

logger = logging.getLogger("api_logger")

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
SECRET_KEY = os.getenv("SECRET_KEY") 
ALGORITHM = "HS256"

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
def get_current_user(db: Session = Depends(get_db), authorization: str = Header(...)):
    # 1. Проверяем формат заголовка (должен быть "Bearer <token>")
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Неверный формат заголовка авторизации")
    
    token = authorization.split(" ")[1]
    
    try:
        # 2. Расшифровываем токен
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        
        # 3. Извлекаем id пользователя (поле "sub" из полезной нагрузки)
        user_id: str = payload.get("sub")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Токен не содержит ID пользователя")
            
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Срок действия токена истек")
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Невалидный токен")

    # 4. Ищем пользователя в базе по ID из токена
    user = db.query(models.User).filter(models.User.id == int(user_id)).first()
    if not user:
        raise HTTPException(status_code=401, detail="Пользователь не найден")
        
    return user

def get_admin_only(user: models.User = Depends(get_current_user)):
    if user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Требуются права администратора")
    return user

def create_access_token(user_id: int):
    expire = datetime.utcnow() + timedelta(days=7)
    to_encode = {"sub": str(user_id), "exp": expire}
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def get_current_user_id(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing token")
    token = authorization.split(" ")[1]
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return int(payload.get("sub"))
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

# --- AUTH (YANDEX) ---
@app.get("/api/v1/auth/yandex/login", tags=["Auth"])
def yandex_login(gameId: str = None):
    params = {
        "response_type": "code",
        "client_id": YANDEX_CLIENT_ID,
        "redirect_uri": YANDEX_REDIRECT_URI,
        "scope": "login:email"
    }
    # 2. Если есть ID игры, упаковываем его в параметр state
    if gameId:
        params["state"] = f"gameId={gameId}"
    base_url = YANDEX_AUTH_URL
    return RedirectResponse(f"{base_url}?{urlencode(params)}")

@app.get("/api/v1/auth/yandex/callback", tags=["Auth"])
async def yandex_callback(code: str, db: Session = Depends(get_db), state: str = None):
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
            logger.exception(f"Ошибка получения токена: {token_resp.text}")
            raise HTTPException(status_code=400, detail="Ошибка обмена кода на токен")
        
        access_token = token_resp.json().get("access_token")
        game_id = ""
        if state and "gameId=" in state:
            # Извлекаем значение из строки "gameId=123"
            game_id = state.split("gameId=")[-1]

        # 2. Получаем данные профиля (ИСПРАВЛЕННЫЙ URL)
        user_info_url = YANDEX_INFO_URL
        user_info_resp = await client.get(
            user_info_url,
            headers={"Authorization": f"OAuth {access_token}"}
        )
        
        if user_info_resp.status_code != 200:
            logger.exception(f"Ошибка данных профиля: {user_info_resp.text}")
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
                        role=UserRole.PLAYER,
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
            logger.exception(f"Database sync error: {e}")
            raise HTTPException(status_code=500, detail="Ошибка сохранения пользователя в базе")

        # 4. Редирект на фронтенд (добавляем provider для консистентности фронта)
        # --- ГЕНЕРАЦИЯ JWT ---
        payload = {
            "sub": str(user.id),
            "email": user.email,
            "role": user.role,
            "exp": datetime.utcnow() + timedelta(days=7) # Токен на неделю
        }
        internal_token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

        # 4. Редирект на фронтенд (теперь передаем token вместо кучи открытых данных)
        # Мы оставляем id/role для удобства фронта, но проверять будем только token
        return RedirectResponse(
            f"{FRONTEND_URL}/?token={internal_token}&id={user.id}&role={user.role}&gameId={game_id}"
        )



# --- GAMES ---

@app.patch("/api/v1/games/{game_id}", response_model=schemas.Game)
def update_game(
    game_id: int, 
    game_update: schemas.GameCreate, 
    db: Session = Depends(get_db), 
    # ЗАМЕНЯЕМ: Вместо заголовка используем зависимость get_current_user
    current_user: models.User = Depends(get_current_user) 
):
    # 1. Получаем игру из базы
    db_game = db.query(models.Game).filter(models.Game.id == game_id).first()
    if not db_game:
        raise HTTPException(status_code=404, detail="Игра не найдена")

    # 2. ПРОВЕРКА ПРАВ (current_user уже проверен внутри Depends)
    is_admin = current_user.role == UserRole.ADMIN
    
    # СОВЕТ: Лучше проверять по creator_id, который мы добавили ранее, 
    # а не по master_name (имя может измениться, а ID — нет)
    is_master_of_game = db_game.creator_id == current_user.id

    if not (is_admin or is_master_of_game):
        raise HTTPException(
            status_code=403, 
            detail="У вас недостаточно прав для редактирования этой игры"
        )

    # 3. Обновление полей
    for key, value in game_update.model_dump().items(): # В Pydantic v2 лучше model_dump()
        setattr(db_game, key, value)

    db.commit()
    db.refresh(db_game)
    return db_game


@app.get("/api/v1/games/{game_id}", response_model=schemas.Game, tags=["Games"])
def get_game(game_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    db_game = db.query(models.Game).filter(models.Game.id == game_id).first()
    if not db_game:
        raise HTTPException(status_code=404, detail="Игра не найдена")
    
    # Добавляем расчет текущего кол-ва игроков для схемы
    db_game.current_players = len(db_game.bookings)
    return db_game

@app.get("/api/v1/games", response_model=List[schemas.Game], tags=["Games"])
def get_games(
    skip: int = Query(0), 
    limit: int = Query(10), 
    search: str = Query(None), 
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user)
):
    query = db.query(models.Game)

    if search:
        query = query.filter(models.Game.title.ilike(f"%{search}%"))

    # Добавляем сортировку по убыванию даты (desc)
    # Сначала самые свежие/будущие игры
    query = query.order_by(models.Game.date_time.desc())

    # Пагинация применяется ПОСЛЕ сортировки
    games = query.offset(skip).limit(limit).all()

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
    if current_user.role not in [UserRole.MASTER, UserRole.ADMIN]:
        raise HTTPException(status_code=403, detail="Недостаточно прав для создания игры")
    
    # Создаем объект игры из пришедших данных
    db_game = models.Game(**game.model_dump())
    
    # ИСПРАВЛЕНО: записываем ID в поле creator_id (как в модели SQLAlchemy)
    db_game.creator_id = current_user.id 
    
    db.add(db_game)
    db.commit()
    db.refresh(db_game)
    
    # Теперь при возврате FastAPI заполнит поле current_players через твой расчет в схеме
    db_game.current_players = 0 
    
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
        is_admin = current_user.role == UserRole.ADMIN
        is_owner = db_game.creator_id == current_user.id
        
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
        logger.exception(f"Ошибка при удалении игры {game_id}: {str(e)}")
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
def leave_game(
    game_id: int, 
    db: Session = Depends(get_db), 
    user: models.User = Depends(get_current_user) # JWT уже нашел юзера
):
    # 1. Используем user.id напрямую из объекта, который вернул JWT-декодер
    booking = db.query(models.Booking).filter(
        models.Booking.game_id == game_id, 
        models.Booking.user_id == user.id
    ).first()
    
    if not booking:
        raise HTTPException(status_code=404, detail="Запись не найдена или вы не записаны")
    
    db.delete(booking)
    db.commit()

    # 2. ОЧЕНЬ ВАЖНО: возвращаем актуальное состояние игры
    # Это нужно фронтенду, чтобы обновить список игроков на экране
    db_game = db.query(models.Game).filter(models.Game.id == game_id).first()
    if db_game:
        # Пересчитываем количество игроков для схемы
        db_game.current_players = len(db_game.bookings)
        return db_game
        
    return {"message": "Вы успешно отписались"}

@app.delete("/api/v1/games/{game_id}/bookings/{user_id}")
def cancel_booking_admin(
    game_id: int,
    user_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    # 1. Ищем игру
    game = db.query(models.Game).filter(models.Game.id == game_id).first()
    if not game:
        raise HTTPException(status_code=404, detail="Игра не найдена")

    # 2. Проверка прав (админ или мастер игры)
    if current_user.role != UserRole.ADMIN and game.creator_id != current_user.id:
        raise HTTPException(status_code=403, detail="Нет прав на удаление игрока")

    # 3. Ищем запись о бронировании
    booking = db.query(models.Booking).filter(
        models.Booking.game_id == game_id,
        models.Booking.user_id == user_id
    ).first()

    if not booking:
        # Если записи нет, просто возвращаем успех (чтобы фронт не падал)
        return {"status": "already_deleted"}

    try:
        # 4. Удаляем
        db.delete(booking)
        
        # 5. ОЧЕНЬ ВАЖНО: Уменьшаем счетчик игроков в модели Game, если он у тебя есть
        if hasattr(game, 'current_players'):
            game.current_players -= 1
            
        db.commit()
        return {"status": "success", "message": "Игрок удален"}
    except Exception as e:
        db.rollback()
        logger.exception(f"Ошибка БД: {e}") # Увидишь в терминале Python
        raise HTTPException(status_code=500, detail="Ошибка базы данных")

# --- ADMIN ---
@app.get("/api/v1/admin/users", response_model=List[schemas.User], tags=["Admin"])
def list_users(
    search: str = Query(None), # Добавляем параметр поиска
    db: Session = Depends(get_db), 
    admin: models.User = Depends(get_admin_only)
):
    query = db.query(models.User)
    
    if search:
        # Фильтруем по email (регистронезависимо)
        query = query.filter(models.User.email.ilike(f"%{search}%"))
        
    return query.all()

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
