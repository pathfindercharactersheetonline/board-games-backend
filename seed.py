from database import SessionLocal
import models

def seed_data():
    db = SessionLocal()
    try:
        # 1. Проверяем, есть ли уже такой админ
        admin_email = "admin@example.com"
        exists = db.query(models.User).filter(models.User.email == admin_email).first()
        
        if not exists:
            # Создаем пользователя-админа
            new_admin = models.User(
                email=admin_email,
                full_name="Главный Администратор",
                role="администратор"
            )
            db.add(new_admin)
            db.commit()
            db.refresh(new_admin)
            
            # Привязываем фейковую идентичность для тестов (чтобы можно было зайти)
            db_identity = models.UserIdentity(
                user_id=new_admin.id,
                provider="system",
                provider_user_id="1"
            )
            db.add(db_identity)
            db.commit()
            print(f"✅ Админ создан! ID: {new_admin.id}, Email: {admin_email}")
        else:
            print("ℹ️ Админ уже существует в базе.")
            
    except Exception as e:
        print(f"❌ Ошибка при наполнении БД: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    seed_data()