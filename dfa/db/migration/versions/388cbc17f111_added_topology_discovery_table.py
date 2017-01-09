"""Added Topology Discovery Table

Revision ID: 388cbc17f111
Revises: 32076c59a4c1
Create Date: 2017-01-04 00:19:06.384325

"""

# revision identifiers, used by Alembic.
revision = '388cbc17f111'
down_revision = '32076c59a4c1'
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.create_table(
        'topology_discovery',
        sa.Column('host', sa.String(255), primary_key=True, nullable=False),
        sa.Column('protocol_interface', sa.String(48), primary_key=True,
                  nullable=False),
        sa.Column('phy_interface', sa.String(48), nullable=True),
        sa.Column('created', sa.DateTime(), nullable=True),
        sa.Column('heartbeat', sa.DateTime(), nullable=True),
        sa.Column('remote_mgmt_addr', sa.String(32), nullable=True),
        sa.Column('remote_system_name', sa.String(128), nullable=True),
        sa.Column('remote_system_desc', sa.String(256), nullable=True),
        sa.Column('remote_port_id_mac', sa.String(17), nullable=True),
        sa.Column('remote_chassis_id_mac', sa.String(17), nullable=True),
        sa.Column('remote_port', sa.String(48), nullable=True),
        sa.Column('remote_evb_cfgd', sa.Boolean(), nullable=True),
        sa.Column('remote_evb_mode', sa.String(16), nullable=True),
        sa.Column('configurations', sa.String(512), nullable=True)
        )


def downgrade():
    op.drop_table('topology_discovery')
