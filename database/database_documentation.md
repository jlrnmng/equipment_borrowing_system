# Database Setup Guide

## Quick Setup with XAMPP

### Step 1: Create Database
1. Open phpMyAdmin: `http://localhost/phpmyadmin`
2. Click "SQL" tab
3. Run this command:
```sql
CREATE DATABASE equipment_borrowing CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

### Step 2: Import Schema
1. Click on `equipment_borrowing` database in the left sidebar
2. Click "Import" tab
3. Choose file: `database/migrations/equipment-borrowing-system`
4. Click "Go" button

This creates all 8 tables and a default admin account.

### Step 3: Import Test Data (Optional)
1. Still in `equipment_borrowing` database
2. Click "Import" tab again
3. Choose file: `database/seeds/test_data.sql`
4. Click "Go" button

This adds 5 test members, 10 equipment items, and 1 additional staff account.

---

## Alternative: Command Line

```bash
# Navigate to XAMPP MySQL bin folder
cd C:\xampp\mysql\bin

# Run schema
mysql -u root -p equipment_borrowing < "C:\Users\rein manaog\Documents\equipment_borrowing_system\database\migrations\equipment_borrowing.sql"

# Run seed data
mysql -u root -p equipment_borrowing < "C:\Users\rein manaog\Documents\equipment_borrowing_system\database\seeds\test_data.sql"
```

---

## Default Login Credentials

**Admin Account:**
- Email: `asogadmin@gmail.com`
- Password: `dostcspcasogtbi`

**Staff Account:**
- Email: `staff@example.com`
- Password: `admin123`

⚠️ **Change the staff password in production!**

---

## Database Structure

### 8 Core Tables Created:

1. **staff** - Staff/admin accounts
2. **members** - Member registrations
3. **equipment** - Equipment inventory
4. **borrow_records** - Borrowing transactions (header)
5. **borrow_items** - Individual borrowed items (details)
6. **violations** - Overdue/damage tracking
7. **notifications** - Email notification queue
8. **activity_log** - System audit trail

### Test Data:

- 5 Members (MEM001 - MEM005)
- 10 Equipment Items (EQ001 - EQ010)
- 2 Staff Accounts (admin + staff)

---

## Verify Installation

Run this in phpMyAdmin SQL tab:
```sql
USE equipment_borrowing;
SHOW TABLES;
SELECT COUNT(*) FROM members;
SELECT COUNT(*) FROM equipment;
```

You should see 8 tables, 5 members, and 10 equipment items.
