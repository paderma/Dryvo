"""empty message

Revision ID: d131b63cdbe0
Revises: b592d959ded3
Create Date: 2019-08-19 18:02:48.592086

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd131b63cdbe0'
down_revision = 'b592d959ded3'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column('cars', sa.Column('color', sa.String(), nullable=True))
    op.add_column('students', sa.Column('car_id', sa.Integer(), nullable=True))
    op.create_foreign_key(None, 'students', 'cars', ['car_id'], ['id'])
    op.add_column('work_days', sa.Column('car_id', sa.Integer(), nullable=True))
    op.create_foreign_key(None, 'work_days', 'cars', ['car_id'], ['id'])
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_constraint(None, 'work_days', type_='foreignkey')
    op.drop_column('work_days', 'car_id')
    op.drop_constraint(None, 'students', type_='foreignkey')
    op.drop_column('students', 'car_id')
    op.drop_column('cars', 'color')
    # ### end Alembic commands ###