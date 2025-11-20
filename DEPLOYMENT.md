# 🚀 Deployment Guide

## Frontend Deployment (Netlify - Free)

1. **Create a Netlify account** at https://netlify.com
2. **Drag and drop** the `frontend/` folder to Netlify
3. **Your site is live!** Netlify will give you a URL like `https://amazing-game-123.netlify.app`

## Backend Deployment (Railway - ~$5/month)

1. **Create a Railway account** at https://railway.app
2. **Connect your GitHub repo** or upload files
3. **Railway will auto-detect** the Python app and deploy it
4. **Get your backend URL** from Railway dashboard

## Alternative: Heroku Backend

1. Install Heroku CLI: `brew install heroku/brew/heroku`
2. Login: `heroku login`
3. Create app: `heroku create your-game-backend`
4. Deploy: `git push heroku main`

## Update Frontend with Backend URL

Once your backend is deployed, update the frontend:

```javascript
// In main.js, replace the dynamic URL section with:
const BACKEND_URL = 'your-backend-domain.railway.app'; // or .herokuapp.com
const WS_PROTOCOL = location.protocol === 'https:' ? 'wss:' : 'ws:';
const WS_URL = `${WS_PROTOCOL}//${BACKEND_URL}`;
```

## Testing

1. **Local testing:** `python3 -m backend.server` + open `frontend/index.html`
2. **Production testing:** Visit your Netlify URL

## Environment Variables

For production, set these in your hosting platform:
- `PORT`: Will be set automatically by Railway/Heroku
- Add any other config variables as needed

## Domain Setup (Optional)

1. **Buy a domain** (e.g., from Namecheap)
2. **Point to Netlify** for frontend
3. **Set up subdomain** for backend (e.g., `api.yourdomain.com`)
