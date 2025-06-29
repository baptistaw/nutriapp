"""add firebase uid to patient

Revision ID: a8f3aff2fe9b
Revises: bedbc1c423c8
Create Date: 2025-06-29 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'a8f3aff2fe9b'
down_revision = 'bedbc1c423c8'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('patient', schema=None) as batch_op:
        batch_op.add_column(sa.Column('firebase_uid', sa.String(length=128), nullable=True))
        batch_op.create_unique_constraint(batch_op.f('uq_patient_firebase_uid'), ['firebase_uid'])


def downgrade():
    with op.batch_alter_table('patient', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('uq_patient_firebase_uid'), type_='unique')
        batch_op.drop_column('firebase_uid')
