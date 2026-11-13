"""Durable notifications for credits issued when an event becomes free."""
from alembic import op
import sqlalchemy as sa

revision = '20261111_promotion_emails'
down_revision = '20261111_promotion_credits'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('event_promotion_email',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('grant_id', sa.Integer(), sa.ForeignKey('credit_grant.id'), nullable=False, unique=True),
        sa.Column('event_id', sa.Integer(), nullable=False),
        sa.Column('recipient', sa.String(150), nullable=False),
        sa.Column('subject', sa.String(255), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('sent_at', sa.DateTime()),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('last_error', sa.String(1024)))
    op.create_index('ix_event_promotion_email_event_id', 'event_promotion_email', ['event_id'])


def downgrade():
    op.drop_index('ix_event_promotion_email_event_id', table_name='event_promotion_email')
    op.drop_table('event_promotion_email')
