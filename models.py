from sqlalchemy import Column, Integer, String, DateTime, Text, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
from database import Base

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    role = Column(String, default="игрок")  # игрок, мастер, администратор
    
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
    date_time = Column(DateTime)
    
    bookings = relationship("Booking", back_populates="game", cascade="all, delete-orphan")

class Booking(Base):
    __tablename__ = "bookings"
    
    id = Column(Integer, primary_key=True, index=True)
    game_id = Column(Integer, ForeignKey("games.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    
    game = relationship("Game", back_populates="bookings")
    user = relationship("User", back_populates="bookings")
