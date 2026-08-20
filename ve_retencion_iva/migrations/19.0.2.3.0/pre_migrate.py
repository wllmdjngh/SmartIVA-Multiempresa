def migrate(cr, version):
    cr.execute("""
        ALTER TABLE ve_wh_iva_prov
        ADD COLUMN IF NOT EXISTS name VARCHAR
    """)
