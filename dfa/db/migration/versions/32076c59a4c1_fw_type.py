"""fw type

Revision ID: 32076c59a4c1
Revises: 3cfc638d80e7
Create Date: 2016-11-22 14:49:44.044868

"""

# revision identifiers, used by Alembic.
revision = '32076c59a4c1'
down_revision = '3cfc638d80e7'
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.add_column('firewall', sa.Column('fw_type', sa.String(length=8), nullable=True))


def downgrade():
    op.drop_column('firewall', 'fw_type')
