# 🗺️ BKMAP - Student Housing Map

<p align="center">
An interactive housing platform that helps university students discover rental rooms through an interactive map, smart search, and location-based recommendations.
</p>

<p align="center">

![React](https://img.shields.io/badge/React-19-61DAFB?logo=react)
![Node.js](https://img.shields.io/badge/Node.js-22-339933?logo=node.js)
![Express](https://img.shields.io/badge/Express.js-000000?logo=express)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-4169E1?logo=postgresql)
![Redis](https://img.shields.io/badge/Redis-DC382D?logo=redis)
![Docker](https://img.shields.io/badge/Docker-2496ED?logo=docker)
![AWS](https://img.shields.io/badge/AWS-EC2-FF9900?logo=amazonaws)
![GitHub Actions](https://img.shields.io/badge/GitHub_Actions-CI/CD-2088FF?logo=githubactions)

</p>

---

## 🌐 Live Demo

**Website:** https://bksmap-tvsv-dut.id.vn

---

## ✨ Key Features

### 🧑‍🎓 Students
- 🔍 Search rooms by location, price, and amenities
- 🗺️ Explore rental rooms on an interactive map
- 📍 Calculate distance from rooms to DUT
- 🏠 View room details and images
- ❤️ Save favorite rooms
- 🔎 Filter and sort room listings

### 🏠 Landlords
- 📝 Create and manage rental listings
- 📸 Upload room images
- ✏️ Edit or delete listings
- 📊 Manage room availability

### 🛡️ Administrators
- 👥 Manage users
- ✅ Moderate room listings
- 🚨 Review reported content
- 📊 Manage the system

---

# 📸 Screenshots

## 🏠 Home Page

<p align="center">
<img width="1920" height="1004" alt="image" src="https://github.com/user-attachments/assets/28b615f4-198d-4e53-a14a-c2d323ad93ce" />


</p>

---

## 🗺️ Interactive Map

<p align="center">
<img width="1920" height="1029" alt="image" src="https://github.com/user-attachments/assets/9b514464-a674-4780-a79a-5958cacbbc70" />

</p>

---

## 🏘️ Room Listings

<p align="center">
<img width="1920" height="1028" alt="image" src="https://github.com/user-attachments/assets/8916d24e-9252-4313-b89f-a0d87eaa4c44" />

</p>

---

# 🏗️ System Architecture

```
                        GitHub Actions
                              │
                              ▼
                    Docker Compose Deployment
                              │
                              ▼
                        AWS EC2 Server
                              │
                   ┌──────────┴──────────┐
                   ▼                     ▼
             Caddy Reverse Proxy     Express API
                                            │
                         ┌──────────────────┴─────────────┐
                         ▼                                ▼
                    PostgreSQL                       Redis Cache
```

---

# 🛠️ Tech Stack

| Category | Technologies |
|----------|--------------|
| Frontend | React, Vite, Tailwind CSS, Zustand, Leaflet |
| Backend | Node.js, Express.js, Prisma ORM |
| Database | PostgreSQL (Supabase) |
| Cache | Redis |
| Authentication | JWT |
| Deployment | AWS EC2, Docker Compose, Caddy |
| CI/CD | GitHub Actions |
| Version Control | Git & GitHub |

---

# 🚀 CI/CD Workflow

The project uses **GitHub Actions** to automate deployment.

Deployment flow:

```
Developer
      │
git push
      │
      ▼
GitHub Actions
      │
      ▼
Build Application
      │
      ▼
Deploy to AWS EC2
      │
      ▼
Restart Docker Containers
      │
      ▼
Production
```

### Infrastructure

- AWS EC2 (t3.small)
- Docker Compose
- Caddy Reverse Proxy
- Automatic HTTPS (SSL)
- Redis
- PostgreSQL

---

# 📄 License

This project was developed for educational purposes at Da Nang University of Science and Technology.

---

prisma npx generate
