"""Limited free event spots and checkout pricing snapshots."""
from alembic import op
import sqlalchemy as sa

revision = '20261111_event_free_spots'
down_revision = '20261027_merch_gallery'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('event', sa.Column('free_spots', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('event_attendee', sa.Column('free_spots', sa.Integer(), nullable=False, server_default='0'))
    op.execute('UPDATE event_attendee SET free_spots = 1 + COALESCE(guest_count, 0) '
               'WHERE event_id IN (SELECT id FROM event WHERE ticket_price <= 0)')
    op.create_table('event_checkout',
        sa.Column('id', sa.String(255), primary_key=True),
        sa.Column('event_id', sa.Integer(), sa.ForeignKey('event.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', sa.String(36)),
        sa.Column('rsvp_id', sa.Integer()),
        sa.Column('quantity', sa.Integer(), nullable=False),
        sa.Column('free_spots', sa.Integer(), nullable=False),
        sa.Column('credits', sa.Integer(), nullable=False),
        sa.Column('amount', sa.Numeric(10, 2), nullable=False),
        sa.Column('completed', sa.Boolean(), nullable=False))


def downgrade():
    op.drop_table('event_checkout')
    op.drop_column('event_attendee', 'free_spots')
    op.drop_column('event', 'free_spots')
