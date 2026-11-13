"""From project root: python prepare_subscription_checkout_migration.py"""
from pathlib import Path
from alembic.config import Config
from alembic.script import ScriptDirectory
import ast

root = Path.cwd()
versions = root / 'migrations' / 'versions'
if not versions.is_dir():
    raise SystemExit('Run from your project folder containing run.py and migrations/.')
revision = '20260927_subscription_checkout'
if any(revision in p.read_text(encoding='utf-8') for p in versions.glob('*.py')):
    raise SystemExit('This migration already exists. Run python -m flask --app run db upgrade.')
config = Config(str(root/'migrations'/'alembic.ini'))
config.set_main_option('script_location', str(root/'migrations'))
heads = ScriptDirectory.from_config(config).get_heads()
if len(heads) != 1:
    raise SystemExit(f'Expected one migration head, found {heads}; merge your migration heads first.')
source = '''"""Store checkout attempts and upgrade recovery state."""
from alembic import op
import sqlalchemy as sa
revision = REVISION
down_revision = HEAD
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('subscription_checkout',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('slot_key', sa.String(80), nullable=True),
        sa.Column('user_id', sa.String(36), sa.ForeignKey('user.id'), nullable=False),
        sa.Column('tier', sa.Integer, nullable=False),
        sa.Column('action', sa.String(20), nullable=False),
        sa.Column('paypal_subscription_id', sa.String(255), nullable=True),
        sa.Column('replaces_subscription_id', sa.String(255), nullable=True),
        sa.Column('payload', sa.Text, nullable=False),
        sa.Column('state', sa.String(30), nullable=False),
        sa.Column('created_at', sa.DateTime, nullable=False),
        sa.Column('updated_at', sa.DateTime, nullable=False),
        sa.Column('last_error', sa.String(255), nullable=True),
        sa.UniqueConstraint('slot_key', name='uq_checkout_slot'),
        sa.UniqueConstraint('paypal_subscription_id', name='uq_checkout_subscription'))
    op.create_index('ix_subscription_checkout_user_id', 'subscription_checkout', ['user_id'])

def downgrade():
    if op.get_bind().execute(sa.text('SELECT COUNT(*) FROM subscription_checkout')).scalar():
        raise RuntimeError('Checkout history exists; use a reviewed rollback that preserves it.')
    op.drop_index('ix_subscription_checkout_user_id', table_name='subscription_checkout')
    op.drop_table('subscription_checkout')
'''.replace('REVISION', repr(revision)).replace('HEAD', repr(heads[0]))
ast.parse(source)
path = versions / (revision + '.py')
with path.open('x', encoding='utf-8') as handle:
    handle.write(source)
print(f'Created {path}. Run python -m flask --app run db upgrade before restarting the site.')
