"""fix_role_enum

Revision ID: e1c966b27186
Revises: 664f03334f84
Create Date: 2026-03-24 00:45:20.152813

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e1c966b27186'
down_revision: Union[str, Sequence[str], None] = '664f03334f84'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Удаляем старый тип, если он завис в базе
    op.execute("DROP TYPE IF EXISTS userrole CASCADE")
    
    # 2. Создаем его заново с ПРАВИЛЬНЫМИ значениями
    # ВАЖНО: убедитесь, что в Python Enum (UserRole) значения такие же
    op.execute("CREATE TYPE userrole AS ENUM ('игрок', 'мастер', 'администратор')")
    
    # 3. Очищаем таблицу пользователей, чтобы старые (некорректные) данные не мешали
    # Если данные ОЧЕНЬ нужны, замените на UPDATE users SET role = 'игрок'
    op.execute("TRUNCATE users CASCADE")
    
    # 4. Выполняем конвертацию
    op.execute("ALTER TABLE users ALTER COLUMN role TYPE userrole USING role::userrole")
    
    # 5. Делаем колонку обязательной
    op.alter_column('users', 'role', nullable=False)



def downgrade() -> None:
    # Возвращаем всё назад, если решим откатиться
    op.execute("ALTER TABLE users ALTER COLUMN role TYPE VARCHAR(255) USING role::text")
    op.execute("DROP TYPE userrole")

