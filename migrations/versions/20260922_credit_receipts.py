"""Add durable subscription credit receipts and deployment cutoff."""
from alembic import op
import sqlalchemy as sa
from datetime import datetime, timezone
revision = '20260922_credit_receipts'
down_revision = '45d9b435c77a'
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('credit_system_state',
        sa.Column('key', sa.String(80), primary_key=True),
        sa.Column('value', sa.String(255), nullable=False))
    op.create_table('subscription_credit_receipt',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('payment_id', sa.Integer, sa.ForeignKey('payment_transaction.id'), nullable=False),
        sa.Column('subscription_id', sa.Integer, sa.ForeignKey('subscription.id'), nullable=False),
        sa.Column('grant_id', sa.Integer, sa.ForeignKey('credit_grant.id'), nullable=True),
        sa.Column('original_amount', sa.Integer, nullable=False),
        sa.Column('payment_time', sa.DateTime, nullable=False),
        sa.Column('mode', sa.String(50), nullable=False),
        sa.Column('issued_at', sa.DateTime, nullable=False),
        sa.UniqueConstraint('payment_id', name='uq_credit_receipt_payment'),
        sa.UniqueConstraint('grant_id', name='uq_credit_receipt_grant'))
    state=sa.table('credit_system_state',sa.column('key',sa.String),sa.column('value',sa.String))
    op.bulk_insert(state,[dict(key='atomic_issuance_started_at',
        value=datetime.now(timezone.utc).isoformat().replace('+00:00','Z'))])

def downgrade():
    count=op.get_bind().execute(sa.text('SELECT COUNT(*) FROM subscription_credit_receipt')).scalar()
    if count:
        raise RuntimeError('Refusing to erase credit deduplication history; use a reviewed rollback plan.')
    op.drop_table('subscription_credit_receipt')
    op.drop_table('credit_system_state')
