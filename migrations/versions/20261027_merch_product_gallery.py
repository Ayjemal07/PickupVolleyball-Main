"""Allow multiple ordered photos on each merchandise listing."""

from alembic import op
import sqlalchemy as sa


revision = '20261027_merch_gallery'
down_revision = '20261027_merch_inventory'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'merch_product_images',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('product_id', sa.String(80),
                  sa.ForeignKey('merch_products.id'), nullable=False),
        sa.Column('image', sa.String(255), nullable=False),
        sa.Column('sort_order', sa.Integer(), nullable=False),
    )
    op.create_index('ix_merch_product_images_product_id',
                    'merch_product_images', ['product_id'])


def downgrade():
    op.drop_index('ix_merch_product_images_product_id',
                  table_name='merch_product_images')
    op.drop_table('merch_product_images')
