"""
Copy source CSV data from parent directory into the project data folder.
This uses the REAL data provided with the problem statement instead of generating synthetic data.
"""
import shutil
import os
import sys

def copy_data():
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    parent_dir = os.path.dirname(project_root)
    data_dir = os.path.join(project_root, "data")
    
    files_to_copy = [
        "sales_history.csv",
        "inventory_snapshot.csv",
        "sku_master.csv",
        "outlet_master.csv",
        "promotions_calendar.csv",
        "festive_calendar.csv",
    ]
    
    copied = 0
    for f in files_to_copy:
        src = os.path.join(parent_dir, f)
        dst = os.path.join(data_dir, f)
        if os.path.exists(src):
            shutil.copy2(src, dst)
            size = os.path.getsize(dst)
            print(f"  ✓ {f} → {size:,} bytes")
            copied += 1
        else:
            print(f"  ✗ {f} NOT FOUND at {src}")
    
    print(f"\n{'='*50}")
    print(f"Copied {copied}/{len(files_to_copy)} files to {data_dir}")
    return copied == len(files_to_copy)

if __name__ == "__main__":
    print("Copying source data files...")
    success = copy_data()
    sys.exit(0 if success else 1)
