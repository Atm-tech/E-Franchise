"""Database schema creation and safe initial data setup."""
from app.core import *  # noqa: F401,F403


def init_db() -> None:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                role TEXT NOT NULL,
                phone TEXT UNIQUE NOT NULL,
                pin TEXT NOT NULL,
                outlet_id INTEGER,
                franchisee_id INTEGER
            );

            CREATE TABLE IF NOT EXISTS franchisees (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                mobile TEXT NOT NULL,
                shop_name TEXT NOT NULL,
                address TEXT,
                area TEXT,
                wallet_balance REAL NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'ACTIVE'
            );

            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sku_code TEXT UNIQUE NOT NULL,
                product_name TEXT NOT NULL,
                department TEXT NOT NULL,
                mrp REAL NOT NULL,
                rsp REAL NOT NULL,
                unit_weight REAL NOT NULL DEFAULT 0,
                weight_unit TEXT NOT NULL DEFAULT 'KG',
                pack_multiplier INTEGER NOT NULL DEFAULT 1,
                commission_mode TEXT NOT NULL DEFAULT 'DEPARTMENT',
                commission_value REAL NOT NULL DEFAULT 0,
                category TEXT NOT NULL DEFAULT '',
                subcategory TEXT NOT NULL DEFAULT '',
                hsn_code TEXT NOT NULL DEFAULT '',
                gst_rate REAL NOT NULL DEFAULT 0,
                price_includes_gst INTEGER NOT NULL DEFAULT 1,
                slab_eligible INTEGER NOT NULL DEFAULT 1,
                variable_weight INTEGER NOT NULL DEFAULT 1,
                pack_label TEXT NOT NULL DEFAULT '1 kg',
                status TEXT NOT NULL DEFAULT 'ACTIVE'
            );

            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_number TEXT UNIQUE NOT NULL,
                franchisee_id INTEGER NOT NULL,
                outlet_id INTEGER NOT NULL DEFAULT 1,
                delivery_type TEXT NOT NULL,
                fulfillment_mode TEXT NOT NULL,
                status TEXT NOT NULL,
                item_subtotal REAL NOT NULL,
                commission_total REAL NOT NULL,
                delivery_charge REAL NOT NULL,
                total REAL NOT NULL,
                created_at TEXT NOT NULL,
                executed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS order_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER NOT NULL,
                product_id INTEGER NOT NULL,
                qty INTEGER NOT NULL,
                unit_rsp REAL NOT NULL,
                line_total REAL NOT NULL,
                line_weight_kg REAL NOT NULL,
                selected_weight_kg REAL NOT NULL DEFAULT 1,
                product_name_snapshot TEXT NOT NULL DEFAULT '',
                hsn_code TEXT NOT NULL DEFAULT '',
                gst_rate REAL NOT NULL DEFAULT 0,
                taxable_value REAL NOT NULL DEFAULT 0,
                gst_amount REAL NOT NULL DEFAULT 0,
                slab_eligible INTEGER NOT NULL DEFAULT 1,
                pack_label TEXT NOT NULL DEFAULT '',
                commission_per_unit REAL NOT NULL DEFAULT 0,
                line_commission REAL NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS bills (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bill_number TEXT UNIQUE NOT NULL,
                order_id INTEGER NOT NULL,
                franchisee_id INTEGER NOT NULL,
                outlet_id INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'BILLED',
                expected_weight_kg REAL NOT NULL DEFAULT 0,
                actual_weight_kg REAL,
                weight_variance_kg REAL,
                weight_check_status TEXT NOT NULL DEFAULT 'PENDING',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS dockets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                docket_number TEXT UNIQUE NOT NULL,
                franchisee_id INTEGER NOT NULL,
                outlet_id INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'DOCKET_CREATED',
                expected_weight_kg REAL NOT NULL DEFAULT 0,
                actual_weight_kg REAL,
                weight_variance_kg REAL,
                weight_check_status TEXT NOT NULL DEFAULT 'PENDING',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS docket_bills (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                docket_id INTEGER NOT NULL,
                bill_id INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS handover_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_type TEXT NOT NULL,
                entity_id INTEGER NOT NULL,
                from_role TEXT NOT NULL,
                to_role TEXT NOT NULL,
                notes TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS delivery_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_type TEXT NOT NULL,
                entity_id INTEGER NOT NULL,
                franchisee_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'ASSIGNED',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS wallet_ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                franchisee_id INTEGER NOT NULL,
                entry_type TEXT NOT NULL,
                amount REAL NOT NULL,
                balance_after REAL NOT NULL,
                reference_type TEXT,
                reference_id INTEGER,
                remarks TEXT,
                created_by INTEGER,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS earnings_wallets (
                franchisee_id INTEGER PRIMARY KEY,
                balance REAL NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS earnings_wallet_ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                franchisee_id INTEGER NOT NULL,
                entry_type TEXT NOT NULL,
                amount REAL NOT NULL,
                balance_after REAL NOT NULL,
                reference_type TEXT,
                reference_id INTEGER,
                remarks TEXT,
                created_by INTEGER,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS wallet_transfers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                franchisee_id INTEGER NOT NULL,
                amount REAL NOT NULL,
                source_wallet TEXT NOT NULL,
                destination_wallet TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'COMPLETED',
                created_by INTEGER,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS target_programs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                scope TEXT NOT NULL,
                department TEXT,
                product_id INTEGER,
                metric TEXT NOT NULL DEFAULT 'SALES_AMOUNT',
                period_type TEXT NOT NULL DEFAULT 'MONTHLY',
                timezone TEXT NOT NULL DEFAULT 'Asia/Kolkata',
                reward_type TEXT NOT NULL DEFAULT 'PERCENT_OF_SALES',
                starts_on TEXT,
                ends_on TEXT,
                status TEXT NOT NULL DEFAULT 'ACTIVE',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS target_slabs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                program_id INTEGER NOT NULL,
                threshold_amount REAL NOT NULL,
                reward_percent REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'ACTIVE',
                created_at TEXT NOT NULL,
                UNIQUE(program_id, threshold_amount)
            );

            CREATE TABLE IF NOT EXISTS franchise_target_progress (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                franchisee_id INTEGER NOT NULL,
                program_id INTEGER NOT NULL,
                period_start TEXT NOT NULL,
                period_end TEXT NOT NULL,
                sales_amount REAL NOT NULL DEFAULT 0,
                achieved_slab_id INTEGER,
                earned_reward REAL NOT NULL DEFAULT 0,
                credited_reward REAL NOT NULL DEFAULT 0,
                credited_at TEXT,
                status TEXT NOT NULL DEFAULT 'ACTIVE',
                updated_at TEXT NOT NULL,
                UNIQUE(franchisee_id, program_id, period_start)
            );

            CREATE TABLE IF NOT EXISTS franchise_advances (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                franchisee_id INTEGER NOT NULL,
                advance_amount REAL NOT NULL,
                wallet_credit_percent REAL NOT NULL,
                wallet_credited_amount REAL NOT NULL,
                received_mode TEXT NOT NULL DEFAULT 'BANK',
                received_at TEXT NOT NULL,
                created_by INTEGER
            );

            CREATE TABLE IF NOT EXISTS franchise_bank_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                franchisee_id INTEGER NOT NULL,
                provider TEXT NOT NULL DEFAULT '',
                provider_account_ref TEXT NOT NULL DEFAULT '',
                account_holder_name TEXT NOT NULL DEFAULT '',
                bank_name TEXT NOT NULL DEFAULT '',
                account_last4 TEXT NOT NULL DEFAULT '',
                ifsc_code TEXT NOT NULL DEFAULT '',
                account_type TEXT NOT NULL DEFAULT '',
                verification_status TEXT NOT NULL DEFAULT 'PENDING',
                status TEXT NOT NULL DEFAULT 'INACTIVE',
                is_primary INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS payment_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                franchisee_id INTEGER NOT NULL,
                bank_account_id INTEGER,
                transaction_type TEXT NOT NULL,
                amount_paise INTEGER NOT NULL,
                currency TEXT NOT NULL DEFAULT 'INR',
                provider TEXT NOT NULL DEFAULT '',
                provider_reference TEXT NOT NULL DEFAULT '',
                idempotency_key TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'CREATED',
                wallet_ledger_id INTEGER,
                failure_code TEXT NOT NULL DEFAULT '',
                failure_message TEXT NOT NULL DEFAULT '',
                initiated_by INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS payment_webhook_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider TEXT NOT NULL,
                provider_event_id TEXT NOT NULL,
                event_type TEXT NOT NULL DEFAULT '',
                payload_hash TEXT NOT NULL DEFAULT '',
                processing_status TEXT NOT NULL DEFAULT 'RECEIVED',
                received_at TEXT NOT NULL,
                processed_at TEXT,
                UNIQUE(provider, provider_event_id)
            );

            CREATE TABLE IF NOT EXISTS otp_challenges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                franchisee_id INTEGER,
                phone_e164 TEXT NOT NULL,
                purpose TEXT NOT NULL,
                provider TEXT NOT NULL DEFAULT '',
                provider_reference TEXT NOT NULL DEFAULT '',
                otp_hash TEXT NOT NULL DEFAULT '',
                attempts INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL DEFAULT 5,
                status TEXT NOT NULL DEFAULT 'PENDING',
                expires_at TEXT NOT NULL,
                verified_at TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS commission_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scope TEXT NOT NULL,
                department TEXT,
                product_id INTEGER,
                commission_type TEXT NOT NULL DEFAULT 'PERCENT',
                commission_value REAL NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'ACTIVE',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS promotions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                scope TEXT NOT NULL,
                department TEXT,
                product_id INTEGER,
                discount_type TEXT NOT NULL DEFAULT 'PERCENT',
                discount_value REAL NOT NULL DEFAULT 0,
                starts_on TEXT,
                ends_on TEXT,
                status TEXT NOT NULL DEFAULT 'ACTIVE',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS order_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                notes TEXT,
                created_by INTEGER,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                actor_id INTEGER,
                actor_role TEXT NOT NULL,
                action TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                entity_id INTEGER,
                details TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS outlets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                code TEXT UNIQUE NOT NULL,
                address TEXT NOT NULL,
                area TEXT NOT NULL,
                phone TEXT,
                status TEXT NOT NULL DEFAULT 'ACTIVE'
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_bills_order_unique ON bills(order_id);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_docket_bill_unique ON docket_bills(bill_id);
            CREATE INDEX IF NOT EXISTS idx_bank_accounts_franchisee ON franchise_bank_accounts(franchisee_id, status);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_bank_provider_ref ON franchise_bank_accounts(provider, provider_account_ref) WHERE provider_account_ref != '';
            CREATE INDEX IF NOT EXISTS idx_payment_franchisee ON payment_transactions(franchisee_id, created_at);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_payment_provider_ref ON payment_transactions(provider, provider_reference) WHERE provider_reference != '';
            CREATE INDEX IF NOT EXISTS idx_otp_phone_status ON otp_challenges(phone_e164, status, expires_at);
            CREATE INDEX IF NOT EXISTS idx_earnings_ledger_franchisee ON earnings_wallet_ledger(franchisee_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_target_progress_franchisee ON franchise_target_progress(franchisee_id, period_start);
            """
        )

        # Lightweight migrations keep existing demo databases usable.
        order_columns = {row["name"] for row in cur.execute("PRAGMA table_info(orders)").fetchall()}
        for column, definition in {
            "customer_name": "TEXT NOT NULL DEFAULT ''",
            "customer_phone": "TEXT NOT NULL DEFAULT ''",
            "customer_address": "TEXT NOT NULL DEFAULT ''",
            "distance_km": "REAL NOT NULL DEFAULT 0",
        }.items():
            if column not in order_columns:
                cur.execute(f"ALTER TABLE orders ADD COLUMN {column} {definition}")

        product_columns = {row["name"] for row in cur.execute("PRAGMA table_info(products)").fetchall()}
        if "image_url" not in product_columns:
            cur.execute("ALTER TABLE products ADD COLUMN image_url TEXT NOT NULL DEFAULT ''")

        def add_columns(table: str, columns: dict[str, str]) -> None:
            existing = {row["name"] for row in cur.execute(f"PRAGMA table_info({table})").fetchall()}
            for column, definition in columns.items():
                if column not in existing:
                    cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

        add_columns("franchisees", {
            "outlet_id": "INTEGER NOT NULL DEFAULT 1",
            "delivery_address": "TEXT NOT NULL DEFAULT ''",
            "bank_account_number": "TEXT NOT NULL DEFAULT ''",
            "ifsc_code": "TEXT NOT NULL DEFAULT ''",
            "bank_name": "TEXT NOT NULL DEFAULT ''",
            "gst_number": "TEXT NOT NULL DEFAULT ''",
            "created_at": "TEXT NOT NULL DEFAULT ''",
        })
        cur.execute("UPDATE franchisees SET delivery_address=address WHERE delivery_address='' AND address!=''")
        add_columns("products", {
            "brand": "TEXT NOT NULL DEFAULT ''",
            "attributes_json": "TEXT NOT NULL DEFAULT '{}'",
            "created_at": "TEXT NOT NULL DEFAULT ''",
            "category": "TEXT NOT NULL DEFAULT ''",
            "subcategory": "TEXT NOT NULL DEFAULT ''",
            "hsn_code": "TEXT NOT NULL DEFAULT ''",
            "gst_rate": "REAL NOT NULL DEFAULT 0",
            "price_includes_gst": "INTEGER NOT NULL DEFAULT 1",
            "slab_eligible": "INTEGER NOT NULL DEFAULT 1",
            "variable_weight": "INTEGER NOT NULL DEFAULT 1",
            "pack_label": "TEXT NOT NULL DEFAULT '1 kg'",
        })
        add_columns("order_items", {
            "selected_weight_kg": "REAL NOT NULL DEFAULT 1",
            "product_name_snapshot": "TEXT NOT NULL DEFAULT ''",
            "hsn_code": "TEXT NOT NULL DEFAULT ''",
            "gst_rate": "REAL NOT NULL DEFAULT 0",
            "taxable_value": "REAL NOT NULL DEFAULT 0",
            "gst_amount": "REAL NOT NULL DEFAULT 0",
            "slab_eligible": "INTEGER NOT NULL DEFAULT 1",
            "pack_label": "TEXT NOT NULL DEFAULT ''",
            "commission_per_unit": "REAL NOT NULL DEFAULT 0",
            "line_commission": "REAL NOT NULL DEFAULT 0",
        })
        add_columns("orders", {
            "wallet_debit_amount": "REAL NOT NULL DEFAULT 0",
            "payable_to_outlet": "REAL NOT NULL DEFAULT 0",
            "updated_at": "TEXT NOT NULL DEFAULT ''",
            "executed_at": "TEXT",
        })
        cur.execute("UPDATE orders SET executed_at=created_at WHERE (executed_at IS NULL OR executed_at='') AND status NOT IN ('ON_HOLD','CANCELLED')")
        add_columns("bills", {
            "pack_status": "TEXT NOT NULL DEFAULT 'UNPACKED'",
            "updated_at": "TEXT NOT NULL DEFAULT ''",
        })
        add_columns("dockets", {
            "updated_at": "TEXT NOT NULL DEFAULT ''",
        })
        add_columns("handover_logs", {
            "from_user_id": "INTEGER",
            "to_user_id": "INTEGER",
            "handover_mode": "TEXT NOT NULL DEFAULT 'MANUAL'",
        })
        add_columns("delivery_tasks", {
            "assigned_user_id": "INTEGER",
            "accepted_at": "TEXT",
            "picked_at": "TEXT",
            "delivered_at": "TEXT",
            "pod_type": "TEXT",
            "pod_reference": "TEXT",
            "remarks": "TEXT",
        })

        operations.migrate(cur)
        dispatch_rules.migrate(cur)

        # Seed defaults without overwriting saved configuration.
        upsert_setting(cur, "wallet_credit_percent", "100")
        cur.execute("INSERT OR IGNORE INTO settings (key,value) VALUES (?,?)", ("wallet_credit_percent", "75"))
        cur.execute("INSERT OR IGNORE INTO settings (key,value) VALUES (?,?)", ("instant_premium_charge", "40"))
        cur.execute("INSERT OR IGNORE INTO settings (key,value) VALUES (?,?)", ("day_delivery_charge", "10"))
        cur.execute("INSERT OR IGNORE INTO settings (key,value) VALUES (?,?)", ("weight_tolerance_kg", "0.30"))
        cur.execute("INSERT OR IGNORE INTO settings (key,value) VALUES (?,?)", ("weight_alert_is_blocking", "0"))
        cur.execute("INSERT OR IGNORE INTO settings (key,value) VALUES (?,?)", ("delivery_per_km", "8"))
        cur.execute("INSERT OR IGNORE INTO settings (key,value) VALUES (?,?)", ("free_delivery_threshold", "999"))
        cur.execute("INSERT OR IGNORE INTO settings (key,value) VALUES (?,?)", ("free_delivery_limit_per_day", "3"))
        cur.execute("INSERT OR IGNORE INTO settings (key,value) VALUES (?,?)", ("low_wallet_threshold", "1000"))
        cur.execute("INSERT OR IGNORE INTO settings (key,value) VALUES (?,?)", ("delivery_charge_bearer", "CUSTOMER"))
        cur.execute("INSERT OR IGNORE INTO settings (key,value) VALUES (?,?)", ("bank_api_enabled", "0"))
        cur.execute("INSERT OR IGNORE INTO settings (key,value) VALUES (?,?)", ("bank_api_provider", ""))
        cur.execute("INSERT OR IGNORE INTO settings (key,value) VALUES (?,?)", ("otp_enabled", "0"))
        cur.execute("INSERT OR IGNORE INTO settings (key,value) VALUES (?,?)", ("otp_provider", ""))
        cur.execute("INSERT OR IGNORE INTO settings (key,value) VALUES (?,?)", ("test_wallet_topup_enabled", "1"))
        cur.execute("INSERT OR IGNORE INTO settings (key,value) VALUES (?,?)", ("business_legal_name", "Aapoorti Shopping Mall Ltd"))
        cur.execute("INSERT OR IGNORE INTO settings (key,value) VALUES (?,?)", ("business_gstin", ""))
        cur.execute("INSERT OR IGNORE INTO settings (key,value) VALUES (?,?)", ("business_address", "Market Road, Sector 12, Bhopal, Madhya Pradesh"))
        cur.execute("INSERT OR IGNORE INTO settings (key,value) VALUES (?,?)", ("business_state_code", "23"))

        # Seed demo content only for a fresh development database.
        if os.getenv("SEED_DEMO", "0" if os.getenv("APP_ENV") == "production" else "1") == "1" and cur.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
            if cur.execute("SELECT COUNT(1) c FROM outlets").fetchone()["c"] == 0:
                cur.executemany(
                    "INSERT INTO outlets (name, code, address, area, phone) VALUES (?, ?, ?, ?, ?)",
                    [
                        ("Aapoorti Central Hub", "OUT-01", "Market Road, Sector 12", "Sector 12", "1800-120-120"),
                        ("Aapoorti North Point", "OUT-02", "High Street, Civil Lines", "Civil Lines", "1800-120-121"),
                        ("Aapoorti East Express", "OUT-03", "Station Road, East Gate", "East Gate", "1800-120-122"),
                    ],
                )

            franchisee_count = cur.execute("SELECT COUNT(1) as c FROM franchisees").fetchone()["c"]
            if franchisee_count == 0:
                cur.execute(
                    """
                    INSERT INTO franchisees (name, mobile, shop_name, address, area, wallet_balance, status)
                    VALUES (?, ?, ?, ?, ?, ?, 'ACTIVE')
                    """,
                    ("Ravi Traders", "9000000002", "Ravi Mart", "Sector 12, Market Road", "Sector 12", 5000.0),
                )

            def ensure_role_user(
                role: str,
                username: str,
                password: str,
                display_name: str,
                franchisee_id: int | None = None,
            ) -> None:
                row = cur.execute("SELECT id FROM users WHERE role=? ORDER BY id ASC LIMIT 1", (role,)).fetchone()
                if row:
                    return
                else:
                    cur.execute(
                        """
                        INSERT INTO users (name, role, phone, pin, outlet_id, franchisee_id)
                        VALUES (?, ?, ?, ?, 1, ?)
                        """,
                        (display_name, role, username, hash_password(password), franchisee_id),
                    )

            default_franchise = cur.execute("SELECT id FROM franchisees ORDER BY id ASC LIMIT 1").fetchone()
            default_franchise_id = default_franchise["id"] if default_franchise else None

            # Demo identities are created once; saved credentials are never reset.
            ensure_role_user("ADMIN", "adm", "adm@123", "Admin User")
            ensure_role_user("FRANCHISEE", "frn", "frn@123", "Franchise User", default_franchise_id)
            ensure_role_user("POS", "pos", "pos@123", "POS User")
            ensure_role_user("GUARD", "grd", "grd@123", "Guard User")
            ensure_role_user("DELIVERY", "dlv", "dlv@123", "Delivery User")

            for account in cur.execute("SELECT id,pin FROM users").fetchall():
                if not account["pin"].startswith("pbkdf2_sha256$"):
                    cur.execute("UPDATE users SET pin=? WHERE id=?", (hash_password(account["pin"]), account["id"]))

            for row in cur.execute("SELECT id, wallet_balance FROM franchisees WHERE wallet_balance != 0").fetchall():
                exists = cur.execute("SELECT 1 FROM wallet_ledger WHERE franchisee_id=? LIMIT 1", (row["id"],)).fetchone()
                if not exists:
                    cur.execute(
                        "INSERT INTO wallet_ledger (franchisee_id, entry_type, amount, balance_after, reference_type, remarks, created_at) VALUES (?, 'OPENING_BALANCE', ?, ?, 'MIGRATION', 'Opening wallet balance', ?)",
                        (row["id"], row["wallet_balance"], row["wallet_balance"], now_str()),
                    )

            product_count = cur.execute("SELECT COUNT(1) as c FROM products").fetchone()["c"]
            if product_count == 0:
                cur.executemany(
                    """
                    INSERT INTO products (sku_code, product_name, department, mrp, rsp, unit_weight, weight_unit, pack_multiplier, commission_mode, commission_value, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE')
                    """,
                    [
                        ("SKU-001", "Toor Dal 1kg", "Grocery", 180, 165, 1.0, "KG", 1, "PRODUCT", 4),
                        ("SKU-002", "Rice 5kg", "Grocery", 750, 699, 5.0, "KG", 1, "DEPARTMENT", 3),
                        ("SKU-003", "Noodle Pack of 5", "Snacks", 150, 135, 0.07, "KG", 5, "PRODUCT", 2),
                    ],
                )
            # Remove the earlier broad demo catalogue; the product master is intentionally curated.
            cur.execute("DELETE FROM products WHERE sku_code LIKE 'SKU-%'")

            # Curated from ALL CAT-6 BASE FILE.xlsx; hierarchy is normalized for assisted retail.
            cur.executemany(
                """INSERT OR IGNORE INTO products
                   (sku_code, product_name, department, category, subcategory, brand, hsn_code, mrp, rsp,
                    unit_weight, weight_unit, pack_multiplier, commission_mode, commission_value,
                    gst_rate, price_includes_gst, slab_eligible, status, attributes_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 'KG', 1, 'PRODUCT', ?, ?, 1, ?, 'ACTIVE', '{}', ?)""",
                [
                    ("8906008819502", "Fortune Refined Soyabean Oil", "Food & Grocery", "Cooking Essentials", "Edible Oils", "Fortune", "150790", 160, 130, 2, 5, 0, now_str()),
                    ("8901262030151", "Amul Pure Ghee", "Food & Grocery", "Cooking Essentials", "Ghee", "Amul", "040590", 650, 650, 2, 5, 0, now_str()),
                    ("23", "Sugar (Loose)", "Food & Grocery", "Staples", "Sugar & Sweeteners", "Unbranded", "1701", 60, 46, 2, 5, 0, now_str()),
                    ("8901030877148", "Red Label Black Tea", "Beverages", "Tea & Infusions", "Black Tea", "Brooke Bond", "09023020", 560, 490, 8, 5, 1, now_str()),
                    ("8719514263161", "Philips Emergency LED Bulb 12W", "Home & Living", "Lighting", "Emergency LED Bulbs", "Philips", "85395000", 689, 449, 8, 18, 1, now_str()),
                    ("A23239020", "Induction Cooktop 2200W", "Electronics & Appliances", "Kitchen Appliances", "Induction Cooktops", "Generic", "8516", 4275, 3199, 8, 18, 1, now_str()),
                ],
            )
            curated_existing = [
                ("Food & Grocery", "Staples", "Rice", "10063010", 0, "35004"),
                ("Food & Grocery", "Staples", "Wheat", "1001", 0, "13098"),
                ("Food & Grocery", "Staples", "Pulses", "07139010", 0, "36042"),
            ]
            for department, category, subcategory, hsn_code, gst_rate, sku_code in curated_existing:
                cur.execute(
                    """UPDATE products SET department=?, category=?, subcategory=?, hsn_code=?, gst_rate=?, price_includes_gst=1,
                                          commission_mode='PRODUCT', commission_value=8
                       WHERE sku_code=? AND (category='' OR category IS NULL)""",
                    (department, category, subcategory, hsn_code, gst_rate, sku_code),
                )
            cur.execute(
                "UPDATE products SET commission_mode='PRODUCT', commission_value=8 WHERE sku_code IN ('35004','13098','36042')"
            )
            # Sale presentation follows the actual pack: only loose commodities can be reweighed.
            pack_profiles = [
                (1, "1 kg", "35004"), (1, "1 kg", "13098"), (1, "1 kg", "36042"),
                (0, "1 L pouch", "8906008819502"), (0, "1 L tin", "8901262030151"),
                (1, "1 kg", "23"), (0, "1 kg pouch", "8901030877148"),
                (0, "1 unit · 12W", "8719514263161"), (0, "1 unit · 2200W", "A23239020"),
            ]
            cur.executemany("UPDATE products SET variable_weight=?, pack_label=? WHERE sku_code=?", pack_profiles)
            cur.execute(
                "UPDATE products SET department='Electronics & Appliances', category='Lighting', commission_value=12 WHERE sku_code='8719514263161'"
            )
            cur.execute(
                "UPDATE products SET brand='Prestige', product_name='Prestige Induction Cooktop 2200W' WHERE sku_code='A23239020'"
            )
            cur.execute("UPDATE products SET commission_value=8 WHERE department='Food & Grocery'")
            cur.execute("UPDATE products SET commission_value=6 WHERE department='Beverages'")
            cur.execute("UPDATE products SET commission_value=12 WHERE department='Electronics & Appliances'")
            cur.execute("UPDATE products SET commission_value=2, slab_eligible=0 WHERE sku_code IN ('8906008819502','8901262030151','23')")
            department_commissions = [
                ("Food & Grocery", 8), ("Beverages", 6), ("Electronics & Appliances", 12),
            ]
            for department, rate in department_commissions:
                exists = cur.execute(
                    "SELECT 1 FROM commission_rules WHERE scope='DEPARTMENT' AND lower(department)=lower(?) AND status='ACTIVE' LIMIT 1",
                    (department,),
                ).fetchone()
                if not exists:
                    cur.execute(
                        "INSERT INTO commission_rules (scope,department,commission_type,commission_value,status,created_at) VALUES ('DEPARTMENT',?,'PERCENT',?,'ACTIVE',?)",
                        (department, rate, now_str()),
                    )
            for sku_code in ("8906008819502", "8901262030151", "23"):
                product = cur.execute("SELECT id FROM products WHERE sku_code=?", (sku_code,)).fetchone()
                if product and not cur.execute(
                    "SELECT 1 FROM commission_rules WHERE scope='PRODUCT' AND product_id=? AND status='ACTIVE' LIMIT 1", (product["id"],)
                ).fetchone():
                    cur.execute(
                        "INSERT INTO commission_rules (scope,product_id,commission_type,commission_value,status,created_at) VALUES ('PRODUCT',?,'PERCENT',2,'ACTIVE',?)",
                        (product["id"], now_str()),
                    )
            cur.execute(
                """UPDATE order_items
                   SET product_name_snapshot=COALESCE(NULLIF(product_name_snapshot,''),(SELECT product_name FROM products WHERE id=order_items.product_id),''),
                       hsn_code=COALESCE(NULLIF(hsn_code,''),(SELECT hsn_code FROM products WHERE id=order_items.product_id),''),
                       gst_rate=CASE WHEN gst_rate=0 THEN COALESCE((SELECT gst_rate FROM products WHERE id=order_items.product_id),0) ELSE gst_rate END,
                       slab_eligible=COALESCE((SELECT slab_eligible FROM products WHERE id=order_items.product_id),slab_eligible),
                       pack_label=COALESCE(NULLIF(pack_label,''),(SELECT pack_label FROM products WHERE id=order_items.product_id),'')
                   WHERE product_name_snapshot='' OR hsn_code=''"""
            )
            cur.execute(
                """UPDATE order_items
                   SET taxable_value=ROUND(CASE WHEN gst_rate>0 THEN line_total*100.0/(100.0+gst_rate) ELSE line_total END,2),
                       gst_amount=ROUND(line_total-(CASE WHEN gst_rate>0 THEN line_total*100.0/(100.0+gst_rate) ELSE line_total END),2)
                   WHERE taxable_value=0 AND line_total>0"""
            )

            cur.execute(
                """INSERT OR IGNORE INTO target_programs
                   (code, name, scope, metric, period_type, timezone, reward_type, status, created_at)
                   VALUES ('MONTHLY-TOTAL-SALES', 'Monthly Total Sales', 'TOTAL_SALE', 'SALES_AMOUNT',
                           'MONTHLY', 'Asia/Kolkata', 'PERCENT_OF_SALES', 'ACTIVE', ?)""",
                (now_str(),),
            )
            cur.execute(
                "UPDATE target_programs SET reward_type='PERCENT_OF_SALES' WHERE code='MONTHLY-TOTAL-SALES' AND reward_type='PERCENT_OF_TARGET'"
            )
            target_program = cur.execute("SELECT id FROM target_programs WHERE code='MONTHLY-TOTAL-SALES'").fetchone()
            if target_program:
                cur.executemany(
                    "INSERT OR IGNORE INTO target_slabs (program_id, threshold_amount, reward_percent, status, created_at) VALUES (?, ?, ?, 'ACTIVE', ?)",
                    [
                        (target_program["id"], 50000, 2, now_str()),
                        (target_program["id"], 100000, 4, now_str()),
                        (target_program["id"], 500000, 10, now_str()),
                    ],
                )
            for franchise_row in cur.execute("SELECT id FROM franchisees").fetchall():
                cur.execute(
                    "INSERT OR IGNORE INTO earnings_wallets (franchisee_id, balance, updated_at) VALUES (?, 0, ?)",
                    (franchise_row["id"], now_str()),
                )
                refresh_target_progress(cur, franchise_row["id"])
        for account in cur.execute("SELECT id,pin FROM users").fetchall():
            if not account["pin"].startswith("pbkdf2_sha256$"):
                cur.execute("UPDATE users SET pin=? WHERE id=?", (hash_password(account["pin"]), account["id"]))
        if os.getenv("APP_ENV") == "production":
            upsert_setting(cur, "test_wallet_topup_enabled", "0")
            for login_id in ("adm", "frn", "pos", "grd", "dlv"):
                existing_demo = cur.execute("SELECT pin FROM users WHERE phone=?", (login_id,)).fetchone()
                if existing_demo and verify_password(login_id + "@123", existing_demo["pin"]):
                    raise RuntimeError("Change all default demo passwords in development before using this database in production")
            if not cur.execute("SELECT 1 FROM users WHERE role='ADMIN'").fetchone():
                login_id, password = os.getenv("BOOTSTRAP_ADMIN_LOGIN"), os.getenv("BOOTSTRAP_ADMIN_PASSWORD", "")
                if not login_id or len(password) < 12 or password.startswith("replace-"):
                    raise RuntimeError("Set BOOTSTRAP_ADMIN_LOGIN and a 12+ character BOOTSTRAP_ADMIN_PASSWORD for a new production database")
                cur.execute("INSERT INTO users(name,role,phone,pin) VALUES('Administrator','ADMIN',?,?)", (login_id, hash_password(password)))
        # Explicit one-time recovery for a locked-out admin. The consumed marker
        # lives in the application database, so a stale Render env flag cannot
        # reset the password again on later restarts.
        reset_token = os.getenv("RESET_BOOTSTRAP_ADMIN", "").strip()
        if reset_token and reset_token not in {"0", "false", "False"} and reset_token != get_setting(cur, "bootstrap_admin_reset_token", ""):
            login_id = os.getenv("BOOTSTRAP_ADMIN_LOGIN", "").strip()
            password = os.getenv("BOOTSTRAP_ADMIN_PASSWORD", "")
            if len(reset_token) < 32 or not login_id or len(password) < 16:
                raise RuntimeError("One-time admin recovery requires a 32+ character reset token, BOOTSTRAP_ADMIN_LOGIN, and a 16+ character BOOTSTRAP_ADMIN_PASSWORD")
            admin = cur.execute("SELECT id FROM users WHERE phone=? AND role='ADMIN'", (login_id,)).fetchone()
            if admin:
                cur.execute("UPDATE users SET pin=?,auth_version=auth_version+1 WHERE id=?", (hash_password(password), admin["id"]))
            elif not cur.execute("SELECT 1 FROM users WHERE role='ADMIN'").fetchone():
                cur.execute("INSERT INTO users(name,role,phone,pin) VALUES('Administrator','ADMIN',?,?)", (login_id, hash_password(password)))
            else:
                raise RuntimeError("Bootstrap admin login does not match an existing admin account")
            upsert_setting(cur, "bootstrap_admin_reset_token", reset_token)
        for f in cur.execute("SELECT id FROM franchisees").fetchall():
            cur.execute("INSERT OR IGNORE INTO earnings_wallets(franchisee_id,balance,updated_at) VALUES(?,0,?)", (f["id"], now_str()))
        conn.commit()
