"""Add detailed user profile fields

Revision ID: 63d70015258b
Revises: 6c7114ed08d7
Create Date: 2025-06-24 10:49:23.654539

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '63d70015258b'
down_revision = '6c7114ed08d7'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.add_column(sa.Column('surname', sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column('profession', sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column('license_number', sa.String(length=50), nullable=True))
        batch_op.add_column(sa.Column('city', sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column('country', sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column('address', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('phone_number', sa.String(length=30), nullable=True))

    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.drop_column('phone_number')
        batch_op.drop_column('address')
        batch_op.drop_column('country')
        batch_op.drop_column('city')
        batch_op.drop_column('license_number')
        batch_op.drop_column('profession')
        batch_op.drop_column('surname')

    # ### end Alembic commands ###
