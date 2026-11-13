"""Track primary spots already compensated when a promotion is enabled."""
from alembic import op
import sqlalchemy as sa

revision = '20261111_promotion_credits'
down_revision = '20261111_promotion_claims'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('event_attendee', sa.Column('promotion_compensated', sa.Boolean(),
                                            nullable=False, server_default=sa.false()))


def downgrade():
    op.drop_column('event_attendee', 'promotion_compensated')
