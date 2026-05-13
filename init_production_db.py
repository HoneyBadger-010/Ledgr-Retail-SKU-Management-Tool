#!/usr/bin/env python3
"""
init_production_db.py — Force database initialization and seeding for production

Run this script on Render to seed the production database:
  python init_production_db.py

This script:
1. Checks if the database is empty
2. Forces a fresh seed from CSV files
3. Verifies the data was loaded correctly
4. Provides detailed logging for debugging
"""
import os
import sys
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('init_db')

def main():
    """Initialize and seed the production database."""
    try:
        # Import Flask app to get database context
        from app import app
        from database import db
        from models import SKU, Outlet, Store, InventorySnapshot
        
        logger.info("=" * 60)
        logger.info("PRODUCTION DATABASE INITIALIZATION")
        logger.info("=" * 60)
        
        with app.app_context():
            # Check current database state
            logger.info(f"Database URI: {app.config.get('SQLALCHEMY_DATABASE_URI', 'NOT SET')}")
            
            sku_count = SKU.query.count()
            outlet_count = Outlet.query.count()
            store_count = Store.query.count()
            inv_count = InventorySnapshot.query.count()
            
            logger.info(f"Current database state:")
            logger.info(f"  - Stores: {store_count}")
            logger.info(f"  - SKUs: {sku_count}")
            logger.info(f"  - Outlets: {outlet_count}")
            logger.info(f"  - Inventory Snapshots: {inv_count}")
            
            if sku_count == 0:
                logger.warning("Database is EMPTY - forcing seed...")
                
                # Force re-seed by calling the internal seed function
                from database import _seed_if_empty
                _seed_if_empty()
                
                # Verify seeding worked
                sku_count = SKU.query.count()
                outlet_count = Outlet.query.count()
                store_count = Store.query.count()
                inv_count = InventorySnapshot.query.count()
                
                logger.info("=" * 60)
                logger.info("SEEDING COMPLETE")
                logger.info("=" * 60)
                logger.info(f"New database state:")
                logger.info(f"  - Stores: {store_count}")
                logger.info(f"  - SKUs: {sku_count}")
                logger.info(f"  - Outlets: {outlet_count}")
                logger.info(f"  - Inventory Snapshots: {inv_count}")
                
                if sku_count > 0:
                    logger.info("✓ Database seeded successfully!")
                    
                    # Show sample data
                    sample_skus = SKU.query.limit(5).all()
                    logger.info("\nSample SKUs:")
                    for sku in sample_skus:
                        logger.info(f"  - {sku.sku_code}: {sku.product_name} ({sku.brand})")
                    
                    return 0
                else:
                    logger.error("✗ Seeding failed - database is still empty!")
                    logger.error("Check that CSV files exist in /app/data/")
                    return 1
            else:
                logger.info("✓ Database already contains data - no seeding needed")
                
                # Show sample data
                sample_skus = SKU.query.limit(5).all()
                logger.info("\nSample SKUs:")
                for sku in sample_skus:
                    logger.info(f"  - {sku.sku_code}: {sku.product_name} ({sku.brand})")
                
                return 0
                
    except Exception as e:
        logger.error(f"✗ Database initialization failed: {e}")
        logger.exception("Full traceback:")
        return 1

if __name__ == "__main__":
    sys.exit(main())
