from sqlalchemy import Column, Integer, String, DateTime, Text, ForeignKey, UniqueConstraint, Enum as SQLEnum
from sqlalchemy.orm import relationship
from database import Base
import enum

class UserRole(str, enum.Enum):
    PLAYER = "игрок"
    MASTER = "мастер"
    ADMIN = "администратор"

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    role = Column(
        SQLEnum(UserRole, values_callable=lambda obj: [e.value for e in obj]),
        default=UserRole.PLAYER,
        nullable=False
    )
    
    # Поля для OAuth (теперь здесь)
    auth_provider = Column(String, nullable=True) # например, "yandex"
    provider_user_id = Column(String, unique=True, index=True, nullable=True)

    # Связи остаются прежними
    bookings = relationship("Booking", back_populates="user")

class Game(Base):
    __tablename__ = "games"
    
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, index=True)
    master_name = Column(String)
    image_url = Column(String)
    description = Column(Text)
    max_players = Column(Integer)
    date_time = Column(DateTime, index=True)
    creator_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    creator = relationship("User", foreign_keys=[creator_id])
    
    bookings = relationship("Booking", back_populates="game", cascade="all, delete-orphan")

class Booking(Base):
    __tablename__ = "bookings"
    __table_args__ = (UniqueConstraint('game_id', 'user_id', name='_game_user_uc'),)
    
    id = Column(Integer, primary_key=True, index=True)
    game_id = Column(Integer, ForeignKey("games.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    
    game = relationship("Game", back_populates="bookings")
    user = relationship("User", back_populates="bookings")
