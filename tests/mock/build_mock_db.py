"""Seeded SQLite replica of the lab pipeline schema.

Used by tests/run_against_mock.py to execute the real validator code paths
end-to-end without lab connectivity. Deliberately contains seeded defects.
"""
import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parent / "mock_pipeline.db"
BD = "2026-05-01"


def build():
    if DB.exists():
        DB.unlink()
    c = sqlite3.connect(DB)
    c.executescript("""
    CREATE TABLE retail_sales_raw (
      transaction_id TEXT PRIMARY KEY, sale_date TEXT, branch_code TEXT, customer_id TEXT,
      product_code TEXT, product_name_raw TEXT, product_type_raw TEXT, policy_number TEXT,
      folio_number TEXT, gross_amount REAL, discount_amount REAL, transaction_status TEXT,
      salesperson_id TEXT, payment_mode TEXT, created_at TEXT);
    CREATE TABLE customer_master_raw (
      customer_id TEXT PRIMARY KEY, customer_name TEXT, dob TEXT, gender TEXT, city TEXT,
      state TEXT, mobile TEXT, email TEXT, kyc_status TEXT, created_at TEXT);
    CREATE TABLE product_master_raw (
      product_code TEXT PRIMARY KEY, standard_product_name TEXT, standard_product_type TEXT,
      product_category TEXT, issuer_company TEXT, active_flag INTEGER, created_at TEXT);
    CREATE TABLE branch_region_mapping_raw (
      branch_code TEXT PRIMARY KEY, branch_name TEXT, city TEXT, state TEXT,
      region_name TEXT, active_flag INTEGER);
    CREATE TABLE distributor_sales_raw (
      distributor_txn_id TEXT PRIMARY KEY, sale_date TEXT, distributor_code TEXT,
      customer_id TEXT, product_code TEXT, product_name_raw TEXT, product_type_raw TEXT,
      policy_number TEXT, folio_number TEXT, gross_amount REAL, discount_amount REAL,
      commission_amount REAL, transaction_status TEXT, region_code TEXT, created_at TEXT);
    CREATE TABLE online_sales_raw (
      transaction_id TEXT PRIMARY KEY, sale_date TEXT, customer_id TEXT, product_code TEXT,
      product_name_raw TEXT, product_type_raw TEXT, gross_amount REAL, discount_amount REAL,
      transaction_status TEXT, region_code TEXT, payment_mode TEXT, created_at TEXT);

    CREATE TABLE stg_retail_sales (
      transaction_id TEXT, sale_date TEXT, branch_code TEXT, customer_id TEXT,
      product_code TEXT, product_name_raw TEXT, product_type_raw TEXT, policy_number TEXT,
      folio_number TEXT, gross_amount REAL, discount_amount REAL, transaction_status TEXT,
      salesperson_id TEXT, payment_mode TEXT, created_at TEXT,
      load_batch_id TEXT, loaded_at TEXT);
    CREATE TABLE stg_distributor_sales (
      transaction_id TEXT, sale_date TEXT, distributor_code TEXT, customer_id TEXT,
      product_code TEXT, product_name_raw TEXT, product_type_raw TEXT, policy_number TEXT,
      folio_number TEXT, gross_amount REAL, discount_amount REAL, commission_amount REAL,
      transaction_status TEXT, region_code TEXT, created_at TEXT,
      load_batch_id TEXT, loaded_at TEXT);
    CREATE TABLE stg_online_sales (
      transaction_id TEXT, sale_date TEXT, customer_id TEXT, product_code TEXT,
      product_name_raw TEXT, product_type_raw TEXT, gross_amount REAL, discount_amount REAL,
      transaction_status TEXT, region_code TEXT, payment_mode TEXT, created_at TEXT,
      load_batch_id TEXT, loaded_at TEXT);
    CREATE TABLE stg_customer_master (
      customer_id TEXT, customer_name TEXT, dob TEXT, gender TEXT, city TEXT, state TEXT,
      mobile TEXT, email TEXT, kyc_status TEXT, created_at TEXT,
      load_batch_id TEXT, loaded_at TEXT);
    CREATE TABLE stg_product_master (
      product_code TEXT, standard_product_name TEXT, standard_product_type TEXT,
      product_category TEXT, issuer_company TEXT, active_flag INTEGER, created_at TEXT,
      load_batch_id TEXT, loaded_at TEXT);
    CREATE TABLE stg_branch_region_mapping (
      branch_code TEXT, branch_name TEXT, city TEXT, state TEXT, region_name TEXT,
      active_flag INTEGER, load_batch_id TEXT, loaded_at TEXT);

    CREATE TABLE dm_sales_transaction (
      sales_transaction_id TEXT PRIMARY KEY, source_channel TEXT, sale_date TEXT,
      customer_id TEXT, customer_name_clean TEXT, customer_state TEXT, region_name TEXT,
      product_code TEXT, standard_product_name TEXT, standard_product_type TEXT,
      product_category TEXT, policy_number TEXT, folio_number TEXT,
      gross_sales_amount REAL, discount_amount REAL, net_sales_amount REAL,
      commission_amount REAL, transaction_status TEXT, load_batch_id TEXT, created_at TEXT);
    CREATE TABLE dm_sales_daily_summary (
      sale_date TEXT PRIMARY KEY, transaction_count INTEGER, total_gross_sales_amount REAL,
      total_net_sales_amount REAL, total_insurance_premium REAL,
      total_mutual_fund_sales REAL, avg_ticket_size REAL);
    CREATE TABLE dm_sales_channel_summary (
      sale_date TEXT, source_channel TEXT, standard_product_type TEXT,
      transaction_count INTEGER, total_gross_sales_amount REAL,
      total_net_sales_amount REAL, avg_ticket_size REAL);
    CREATE TABLE dm_sales_region_summary (
      sale_date TEXT, region_name TEXT, standard_product_type TEXT,
      transaction_count INTEGER, total_gross_sales_amount REAL,
      total_net_sales_amount REAL, avg_ticket_size REAL);
    CREATE TABLE dm_sales_product_summary (
      sale_date TEXT, product_code TEXT, standard_product_name TEXT,
      standard_product_type TEXT, product_category TEXT, transaction_count INTEGER,
      total_gross_sales_amount REAL, total_net_sales_amount REAL, avg_ticket_size REAL);
    CREATE TABLE dm_executive_sales_summary (
      sale_date TEXT PRIMARY KEY, total_transactions INTEGER, total_insurance_premium REAL,
      total_mutual_fund_sales REAL, total_net_sales_amount REAL, avg_ticket_size REAL,
      top_region TEXT, top_channel TEXT);

    CREATE VIEW vw_executive_dashboard AS SELECT * FROM dm_executive_sales_summary;
    CREATE VIEW vw_channel_performance  AS SELECT * FROM dm_sales_channel_summary;
    CREATE VIEW vw_region_performance   AS SELECT * FROM dm_sales_region_summary;
    CREATE VIEW vw_product_performance  AS SELECT * FROM dm_sales_product_summary;
    CREATE VIEW vw_daily_sales_trend    AS SELECT * FROM dm_sales_daily_summary;
    """)

    c.executemany("INSERT INTO product_master_raw VALUES (?,?,?,?,?,?,?)", [
        ("INS_LIFE_001", "Life Secure Plan", "INSURANCE", "Life Insurance", "MM Life", 1, BD),
        ("INS_ULIP_004", "Wealth ULIP Plan", "INSURANCE", "ULIP", "MM Life", 1, BD),
        ("INS_HEALTH_002", "Health Plus", "INSURANCE", "Health Insurance", "MM Health", 1, BD),
        ("MF_EQ_LARGE_001", "Bluechip Equity Fund", "MUTUAL_FUND", "Equity Fund", "MM AMC", 1, BD),
        ("MF_DEBT_GILT_002", "Gilt Saver Fund", "MUTUAL_FUND", "Debt Fund", "MM AMC", 1, BD),
        ("MF_HYB_003", "Balanced Advantage", "MUTUAL_FUND", "Hybrid Fund", "MM AMC", 1, BD),
        ("MF_EQ_OLD_014", "Legacy Equity Fund", "MUTUAL_FUND", "Equity Fund", "MM AMC", 0, BD),
    ])
    c.executemany("INSERT INTO branch_region_mapping_raw VALUES (?,?,?,?,?,?)", [
        ("BR_MUM_042", "Mumbai Andheri", "Mumbai", "Maharashtra", "WEST", 1),
        ("BR_DEL_011", "Delhi CP", "Delhi", "Delhi", "NORTH", 1),
        ("BR_BLR_007", "Bengaluru MG Road", "Bengaluru", "Karnataka", "SOUTH", 1),
        ("BR_KOL_003", "Kolkata Park St", "Kolkata", "West Bengal", "EAST", 1),
    ])
    c.executemany("INSERT INTO customer_master_raw VALUES (?,?,?,?,?,?,?,?,?,?)", [
        ("CUST0112", "  mr. rajesh  kumar ", "1985-04-12", "M", "Mumbai", "Maharashtra",
         "9876543210", "r.kumar@example.com", "VERIFIED", BD),
        ("CUST0113", "Priya Sharma", "1990-08-03", "F", "Delhi", "Delhi",
         "+919812345678", "p.sharma@example.com", "VERIFIED", BD),
        ("CUST0114", "Amit Verma", "1978-01-22", "M", "Bengaluru", "Karnataka",
         "9812345670", "a.verma@example.com", "REJECTED", BD),
        ("CUST0115", "Sneha Iyer", "1995-11-30", "F", "Chennai", "Mahrashtra",
         "98765", "s.iyer@example.com", "VERIFIED", BD),
        ("CUST0116", "Vikram Rao", "1982-06-18", "M", "Kolkata", "West Bengal",
         "9123456780", "v.rao@example.com", "VERIFIED", BD),
    ])

    retail = [
        ("RTL20260501001", BD, "BR_MUM_042", "CUST0112", "INS_LIFE_001", "Life Secure",
         "LI", "POL001", None, 50000.0, 1000.0, "COMPLETED", "SP01", "NEFT", BD),
        ("RTL20260501002", BD, "BR_DEL_011", "CUST0113", "MF_EQ_LARGE_001", "Bluechip",
         "MF", None, "FOL002", 75000.0, 0.0, "COMPLETED", "SP02", "UPI", BD),
        ("RTL20260501003", BD, "BR_MUM_042", "CUST0114", "INS_ULIP_004", "Wealth ULIP",
         "ULIP", "POL003", None, 120000.0, 2000.0, "COMPLETED", "SP01", "CHEQUE", BD),
        ("RTL20260501004", BD, "BR_BLR_007", "CUST0115", "MF_DEBT_GILT_002", "Gilt Saver",
         "Gilt Fund", None, "FOL004", 25000.0, 500.0, "COMPLETED", "SP03", "UPI", BD),
        ("RTL20260501005", BD, "BR_KOL_003", "CUST0116", "MF_HYB_003", "Balanced",
         "Hybrid Fund", None, "FOL005", 55000.0, 1000.0, "COMPLETED", "SP04", "NEFT", BD),
        ("RTL20260501006", BD, "BR_MUM_042", "CUST0112", "INS_HEALTH_002", "Health Plus",
         "Health Insurance", "POL006", None, 30000.0, 0.0, "CANCELLED", "SP01", "CARD", BD),
    ]
    c.executemany("INSERT INTO retail_sales_raw VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", retail)
    # SEEDED DEFECT: the CANCELLED row leaks into staging
    c.executemany("INSERT INTO stg_retail_sales VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  [r + ("BATCH_01", BD) for r in retail])

    dist = [
        ("DST20260501101", BD, "DIST_A", "CUST0112", "INS_LIFE_001", "Life Secure", "LI",
         "POL101", None, 40000.0, 500.0, 2000.0, "APPROVED", "WEST", BD),
        ("DST20260501102", BD, "DIST_B", None, "MF_EQ_LARGE_001", "Bluechip", "MF",
         None, "FOL102", 60000.0, 0.0, 3000.0, "APPROVED", "NORTH", BD),
        ("DST20260501103", BD, "DIST_C", "CUST0116", "MF_EQ_OLD_014", "Legacy Equity",
         "Equity Fund", None, "FOL103", 35000.0, 0.0, 1500.0, "APPROVED", "NORTHEAST", BD),
        ("DST20260501104", BD, "DIST_A", "CUST0113", "INS_LIFE_001", "Life Secure", "LI",
         "POL104", None, 45000.0, 1000.0, 2200.0, "REJECTED", "WEST", BD),
    ]
    c.executemany("INSERT INTO distributor_sales_raw VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", dist)
    c.executemany("INSERT INTO stg_distributor_sales VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  [d + ("BATCH_01", BD) for d in dist if d[12] == "APPROVED"])

    online = [
        ("ONL20260501201", BD, "CUST0115", "MF_HYB_003", "Balanced", "Hybrid Fund",
         28000.0, 0.0, "COMPLETED", "SOUTH", "UPI", BD),
        ("ONL20260501202", BD, "CUST0116", "INS_LIFE_001", "Life Secure", "Life Ins",
         32000.0, 500.0, "COMPLETED", "EAST", "CARD", BD),
        ("ONL20260501203", BD, "CUST0113", "MF_EQ_LARGE_001", "Bluechip", "Equity Fund",
         41000.0, 1000.0, "PENDING", "NORTH", "UPI", BD),
    ]
    c.executemany("INSERT INTO online_sales_raw VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", online)
    c.executemany("INSERT INTO stg_online_sales VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  [o + ("BATCH_01", BD) for o in online if o[8] == "COMPLETED"])

    c.execute("INSERT INTO stg_customer_master SELECT *, 'BATCH_01', ? FROM customer_master_raw",
              (BD,))
    c.execute("INSERT INTO stg_product_master SELECT *, 'BATCH_01', ? FROM product_master_raw",
              (BD,))
    c.execute("INSERT INTO stg_branch_region_mapping SELECT *, 'BATCH_01', ? "
              "FROM branch_region_mapping_raw", (BD,))

    # SEEDED DEFECTS in the data mart: ULIP as MUTUAL_FUND, REJECTED-KYC revenue,
    # null-customer revenue, inactive product sold, UNKNOWN_REGION
    dm = [
        ("RET_RTL20260501001", "Retail", BD, "CUST0112", "Rajesh Kumar", "Maharashtra",
         "WEST", "INS_LIFE_001", "Life Secure Plan", "INSURANCE", "Life Insurance",
         "POL001", None, 50000.0, 1000.0, 49000.0, 0.0, "COMPLETED", "BATCH_01", BD),
        ("RET_RTL20260501002", "Retail", BD, "CUST0113", "Priya Sharma", "Delhi",
         "NORTH", "MF_EQ_LARGE_001", "Bluechip Equity Fund", "MUTUAL_FUND", "Equity Fund",
         None, "FOL002", 75000.0, 0.0, 75000.0, 0.0, "COMPLETED", "BATCH_01", BD),
        ("RET_RTL20260501003", "Retail", BD, "CUST0114", "Amit Verma", "Karnataka",
         "WEST", "INS_ULIP_004", "Wealth ULIP Plan", "MUTUAL_FUND", "ULIP",
         "POL003", None, 120000.0, 2000.0, 118000.0, 0.0, "COMPLETED", "BATCH_01", BD),
        ("RET_RTL20260501004", "Retail", BD, "CUST0115", "Sneha Iyer", "Mahrashtra",
         "SOUTH", "MF_DEBT_GILT_002", "Gilt Saver Fund", "MUTUAL_FUND", "Debt Fund",
         None, "FOL004", 25000.0, 500.0, 24500.0, 0.0, "COMPLETED", "BATCH_01", BD),
        ("RET_RTL20260501005", "Retail", BD, "CUST0116", "Vikram Rao", "West Bengal",
         "EAST", "MF_HYB_003", "Balanced Advantage", "MUTUAL_FUND", "Hybrid Fund",
         None, "FOL005", 55000.0, 1000.0, 54000.0, 0.0, "COMPLETED", "BATCH_01", BD),
        ("DST_DST20260501101", "Distributor", BD, "CUST0112", "Rajesh Kumar", "Maharashtra",
         "WEST", "INS_LIFE_001", "Life Secure Plan", "INSURANCE", "Life Insurance",
         "POL101", None, 40000.0, 500.0, 39500.0, 2000.0, "APPROVED", "BATCH_01", BD),
        ("DST_DST20260501102", "Distributor", BD, None, None, None,
         "NORTH", "MF_EQ_LARGE_001", "Bluechip Equity Fund", "MUTUAL_FUND", "Equity Fund",
         None, "FOL102", 60000.0, 0.0, 60000.0, 3000.0, "APPROVED", "BATCH_01", BD),
        ("DST_DST20260501103", "Distributor", BD, "CUST0116", "Vikram Rao", "West Bengal",
         "UNKNOWN_REGION", "MF_EQ_OLD_014", "Legacy Equity Fund", "MUTUAL_FUND", "Equity Fund",
         None, "FOL103", 35000.0, 0.0, 35000.0, 1500.0, "APPROVED", "BATCH_01", BD),
        ("ONL_ONL20260501201", "Online", BD, "CUST0115", "Sneha Iyer", "Mahrashtra",
         "UNKNOWN_REGION", "MF_HYB_003", "Balanced Advantage", "MUTUAL_FUND", "Hybrid Fund",
         None, None, 28000.0, 0.0, 28000.0, 0.0, "COMPLETED", "BATCH_01", BD),
        ("ONL_ONL20260501202", "Online", BD, "CUST0116", "Vikram Rao", "West Bengal",
         "EAST", "INS_LIFE_001", "Life Secure Plan", "INSURANCE", "Life Insurance",
         None, None, 32000.0, 500.0, 31500.0, 0.0, "COMPLETED", "BATCH_01", BD),
    ]
    c.executemany("INSERT INTO dm_sales_transaction VALUES "
                  "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", dm)

    c.execute("""INSERT INTO dm_sales_daily_summary
        SELECT sale_date, COUNT(*), SUM(gross_sales_amount), SUM(net_sales_amount),
               SUM(CASE WHEN standard_product_type='INSURANCE' THEN net_sales_amount ELSE 0 END),
               SUM(CASE WHEN standard_product_type='MUTUAL_FUND' THEN net_sales_amount ELSE 0 END),
               ROUND(SUM(net_sales_amount)/COUNT(*),2)
        FROM dm_sales_transaction GROUP BY sale_date""")
    c.execute("""INSERT INTO dm_sales_channel_summary
        SELECT sale_date, source_channel, standard_product_type, COUNT(*),
               SUM(gross_sales_amount), SUM(net_sales_amount),
               ROUND(SUM(net_sales_amount)/COUNT(*),2)
        FROM dm_sales_transaction GROUP BY sale_date, source_channel, standard_product_type""")
    c.execute("""INSERT INTO dm_sales_region_summary
        SELECT sale_date, region_name, standard_product_type, COUNT(*),
               SUM(gross_sales_amount), SUM(net_sales_amount),
               ROUND(SUM(net_sales_amount)/COUNT(*),2)
        FROM dm_sales_transaction GROUP BY sale_date, region_name, standard_product_type""")
    # SEEDED DEFECT: product summary understated by 5000
    c.execute("""INSERT INTO dm_sales_product_summary
        SELECT sale_date, product_code, standard_product_name, standard_product_type,
               product_category, COUNT(*), SUM(gross_sales_amount),
               SUM(net_sales_amount) - CASE WHEN product_code='MF_HYB_003' THEN 5000 ELSE 0 END,
               ROUND(SUM(net_sales_amount)/COUNT(*),2)
        FROM dm_sales_transaction
        GROUP BY sale_date, product_code, standard_product_name, standard_product_type,
                 product_category""")
    c.execute("""INSERT INTO dm_executive_sales_summary
        SELECT sale_date, COUNT(*),
               SUM(CASE WHEN standard_product_type='INSURANCE' THEN net_sales_amount ELSE 0 END),
               SUM(CASE WHEN standard_product_type='MUTUAL_FUND' THEN net_sales_amount ELSE 0 END),
               SUM(net_sales_amount), ROUND(SUM(net_sales_amount)/COUNT(*),2),
               'WEST', 'Retail'
        FROM dm_sales_transaction GROUP BY sale_date""")

    c.commit()
    c.close()
    return DB


if __name__ == "__main__":
    print("mock database built at:", build())
