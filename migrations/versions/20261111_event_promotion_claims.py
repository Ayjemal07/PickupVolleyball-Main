"""Durable per-email promotion redemption across member and guest checkout."""
from alembic import op
import sqlalchemy as sa

revision = '20261111_promotion_claims'
down_revision = '20261111_event_free_spots'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('event_checkout', sa.Column('email', sa.String(150), nullable=True))
    op.create_table('event_promotion_claim',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('event_id', sa.Integer(), sa.ForeignKey('event.id', ondelete='CASCADE'), nullable=False),
        sa.Column('email', sa.String(150), nullable=False),
        sa.Column('user_id', sa.String(36)),
        sa.Column('benefit', sa.String(20), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('event_id', 'email', name='uq_event_promotion_email'))
    op.execute("INSERT INTO event_promotion_claim (event_id, email, user_id, benefit, created_at) "
               "SELECT a.event_id, LOWER(TRIM(COALESCE(u.email, a.email))), MIN(a.user_id), 'free', CURRENT_TIMESTAMP "
               "FROM event_attendee a LEFT JOIN user u ON u.id = a.user_id "
               "WHERE a.free_spots > 0 AND COALESCE(u.email, a.email) IS NOT NULL "
               "GROUP BY a.event_id, LOWER(TRIM(COALESCE(u.email, a.email)))")


def downgrade():
    op.drop_table('event_promotion_claim')
    op.drop_column('event_checkout', 'email')
