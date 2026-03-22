# Getting Started with FinanzasPersonalesIA

Your personal finance application is completely built and ready to use. Follow this guide to get up and running in minutes.

---

## 🚀 Step 1: Start the Server (1 minute)

### Windows Users
Double-click this file:
```
run_server.bat
```

Or open PowerShell/Command Prompt and run:
```bash
cd E:\FinanzasPersonalesIA
python manage.py runserver
```

### Mac/Linux Users
```bash
cd E:\FinanzasPersonalesIA
python manage.py runserver
```

### What You Should See
```
Starting development server at http://127.0.0.1:8000/
Quit the server with CTRL-BREAK (or CTRL-C).
```

---

## 🔓 Step 2: Login (2 minutes)

1. Open your browser
2. Go to: **http://localhost:8000/dashboard/**
3. You will be redirected to login page
4. Enter credentials:
   - **Username:** `demo_user`
   - **Password:** `demo1234`
5. Click "Login"

> Tip: Check "Remember me" to stay logged in longer

---

## 📊 Step 3: Explore the Dashboard (5 minutes)

You're now on the main dashboard! Here's what you see:

### Top Section - Key Metrics
- **💰 Total Patrimonio:** Your net worth (assets - liabilities)
- **📈 Total Activos:** Everything you own
- **📉 Total Pasivos:** Everything you owe
- **💧 Liquidez %:** Percentage of assets you can access immediately

### Middle Section - Charts
- **Income vs Expenses Chart:** See your monthly money flow
- **Expense Distribution:** Pie chart showing where your money goes

### Bottom Section - Quick Links
- Recent departments (rental properties)
- Recent investments
- Quick access to other modules

---

## 📋 Navigation Menu

Click the menu icon (☰) on the top-left to see navigation options:

| Menu Item | What It Does |
|-----------|-------------|
| **Dashboard** | Overview of your finances (you're here!) |
| **Gastos** | View and edit monthly expenses/income |
| **Patrimonio** | Manage assets and liabilities |
| **Departamentos** | Check your 6 rental properties |
| **Inversiones** | Track investments |
| **Configuración** | Settings and exchange rates |
| **Admin** | Advanced administration panel |

---

## 💻 Common Tasks

### Task 1: Check Monthly Expenses

1. Click **"Gastos"** in the menu
2. Select a **month** from the dropdown
3. You'll see all your expenses organized by category:
   - **Ingresos** (Income)
   - **Gastos Fijos** (Fixed Expenses)
   - **Suscripciones** (Subscriptions)
   - **Seguros** (Insurance)
   - **Tarjetas de Crédito** (Credit Cards)
   - **Cobros de Bancos** (Bank Fees)

### Task 2: Edit an Amount

1. On the **Gastos** page, find the amount you want to change
2. Click the **"Edit"** button next to the number
3. Enter the new amount
4. Press **"Save"**
5. The page updates automatically with new totals

### Task 3: Check Your Properties

1. Click **"Departamentos"** in the menu
2. You'll see cards for each property:
   - **E107, E205, E507, F107, F205, F405**
3. Each card shows:
   - Current tenant name
   - Monthly rent
   - Mortgage details
   - ROI percentage
4. Click on a property for more details

### Task 4: View Your Net Worth Over Time

1. Click **"Patrimonio"** in the menu
2. Scroll down to **"Patrimonio History"**
3. You'll see a graph showing how your net worth has changed
4. Click **"Record New Snapshot"** to save today's values

### Task 5: Adjust Exchange Rates

1. Click **"Configuración"** in the menu
2. Scroll to **"Exchange Rates"**
3. Enter today's **USD** and **UF** rates
4. Get rates from: https://mindicador.cl/api
5. Click **"Save"**
6. All calculations update automatically!

---

## 📊 Understanding Your Data

### Sample Data Included

You already have realistic data loaded:

**6 Departments:**
- Location: Miraflores, Santiago
- Prices: $180M - $250M CLP each
- Tenants: All occupied with contracts
- Mortgages: Individual bank loans (Santander, BCI, etc.)

**Monthly Expenses:**
- Services: Gas, Water, Electricity, Internet, etc.
- Credit cards: 16 different cards tracked
- Bank fees: Monthly charges
- Subscriptions: Netflix, Microsoft, etc.

**Investments:**
- Crypto holdings
- Stock investments
- Savings accounts
- Real estate values

**Your Net Worth:**
- Total Assets: ~$1.8B CLP (~$2M USD)
- Total Liabilities: ~$1.2B CLP
- Net Worth: ~$600M CLP (~$700k USD)
- Liquidity: ~3% in cash/crypto

---

## 🔢 Key Metrics Explained

### Dashboard Cards

**Total Patrimonio**
- Calculation: Total Assets - Total Liabilities
- Your actual net worth
- Includes: Cash, investments, properties, loans

**Total Activos**
- Everything you own
- Includes: Cash, investments, property equity
- In both CLP and USD

**Total Pasivos**
- Everything you owe
- Includes: Credit card debt, mortgages, loans
- In both CLP and USD

**Liquidez %**
- Percentage of assets you can access quickly
- Liquid = cash, investments, crypto (usually 3%)
- Non-liquid = properties, long-term holdings (usually 97%)

### Property Cards (Departamentos)

**Arrival (Monthly Rent)**
- How much the tenant pays each month
- Example: $750,000 CLP

**Cap Rate Income-Based**
- Annual rent divided by property value
- Shows cash-on-cash return
- Example: 0.48% means 4.8% annually

**Monthly Amortization**
- How much mortgage principal you pay
- Building equity even if rent doesn't cover all costs
- Example: 3.5 UF per month = ~$180k CLP

**ROI %**
- Combined return from rent, amortization, and appreciation
- Includes capital gains over time

---

## 🛠️ Troubleshooting

### Issue: "Page won't load"

**Solution:**
1. Make sure the server is running (you should see "Starting development server...")
2. Try refreshing the page (Ctrl+F5)
3. Check you're on the right URL: http://localhost:8000

### Issue: "Login doesn't work"

**Solution:**
1. Check username: `demo_user` (exactly)
2. Check password: `demo1234` (exactly)
3. Try clearing browser cookies and logging in again
4. Try in a private/incognito browser window

### Issue: "Exchange rates showing as 0"

**Solution:**
1. Go to **Configuración**
2. Find **"Exchange Rates"** section
3. Enter today's rates manually
4. Get rates from: https://mindicador.cl/api
5. Save

### Issue: "Numbers look wrong"

**Solution:**
1. Check if you're looking at CLP vs USD (top-right of page)
2. Verify the month and year selected
3. Check exchange rates are correct
4. Compare with previous month data

### Issue: "I can't edit a value"

**Solution:**
1. Make sure you clicked **"Edit"** first
2. Some fields in admin are read-only (by design)
3. Try using the "Edit" button on the card
4. If still stuck, go to `/admin/` for full edit access

---

## 🔐 Security & Privacy

Your data is:
- ✅ Stored locally on your computer (not in cloud)
- ✅ Protected by password (Django auth)
- ✅ Isolated per user (multi-user safe)
- ✅ Never shared unless you export it
- ✅ Regularly backed up by the system

**Two-Factor Authentication (2FA):**
- Optional but recommended
- You'll be prompted to set up TOTP on first login
- Use Google Authenticator or Authy app
- Adds extra security layer

---

## 📤 Exporting Your Data

### Export as PDF (Coming Soon)
- Click the **"Export"** button
- Select **"PDF"**
- A report downloads automatically

### Export as Excel (Coming Soon)
- Click the **"Export"** button
- Select **"Excel"**
- Open in Excel or Google Sheets

### Share a Mini-Session (Coming Soon)
- Go to **Patrimonio** > **Mini-Sessions**
- Create a new session (e.g., for a loan to a friend)
- Click **"Generate Shareable Image"**
- Send the image - it hides your bank details!

---

## 🚀 Next Steps

### Today
1. ✅ Start the server
2. ✅ Login with demo_user
3. ✅ Explore the dashboard
4. ✅ Check your properties
5. ✅ Review monthly expenses

### This Week
- [ ] Update this month's expenses
- [ ] Add new categories if needed
- [ ] Update property rental amounts
- [ ] Check investment values
- [ ] Update exchange rates

### This Month
- [ ] Set up 2FA for security
- [ ] Create mini-sessions for loans
- [ ] Review full ROI analysis
- [ ] Export reports for your accountant
- [ ] Plan budget for next month

### Soon (Coming Features)
- Data entry wizard (grouped by bank)
- Automated monthly reminders
- PDF/Excel exports
- Shareable investment reports
- Budget forecasting
- Tax report generation

---

## 📚 Documentation

Need more detailed information?

| Document | Purpose |
|----------|---------|
| `README.md` | Complete project overview |
| `QUICK_START_GUIDE.md` | Quick reference (2-3 minutes) |
| `FRONTEND_DOCUMENTATION.md` | How the frontend works (developers) |
| `API_DOCUMENTATION.md` | REST API reference (developers) |
| `IMPLEMENTATION_STATUS.md` | What's done and what's planned |

---

## ✨ Pro Tips

1. **Keyboard Shortcuts:**
   - `Ctrl+Shift+D` - Go to Dashboard
   - `Ctrl+Shift+G` - Go to Gastos
   - `Ctrl+Shift+P` - Go to Patrimonio

2. **Dark Mode:**
   - Automatically matches your system theme
   - Toggle in **Configuración** settings

3. **Mobile Friendly:**
   - Use your phone to update expenses on the go
   - All forms work great on mobile
   - Responsive design adapts to screen size

4. **Batch Edits:**
   - Update multiple months at once in admin panel
   - Go to `/admin/` for power-user features

5. **Historical Data:**
   - Scroll down on any page to see history
   - Snapshots saved automatically
   - Compare month-to-month trends

---

## 🆘 Need Help?

### Check These First
1. Review this guide (you're reading it!)
2. Go to `/admin/` to see all your data
3. Check the relevant documentation file
4. Review the browser console (F12) for errors

### Common Answers

**Q: How do I change the password?**
A: Go to `/admin/` → Users → Click your user → Change password

**Q: How do I add a new department?**
A: Go to `/admin/` → Departamentos → Add Department (or use the API)

**Q: Can multiple people use this?**
A: Yes! Each person logs in with their own account. Create new users in `/admin/`

**Q: Is my data encrypted?**
A: Yes, when configured for production. It's on your local computer in development.

**Q: Can I backup my data?**
A: Yes! The database is `db.sqlite3`. Copy it to backup location.

---

## 🎉 You're Ready!

Your application is fully functional and waiting for you. 

**Start the server now:** 
```bash
cd E:\FinanzasPersonalesIA
python manage.py runserver
```

**Then open:** http://localhost:8000/dashboard/

**Happy financial tracking!** 💰

---

*Last Updated: March 2024 | Version 1.0 | Built with Django 5.1 + React-inspired Frontend*
