# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Architecture Overview

This is a Telegram bot for KTX (Korean train) reservation automation built with:
- **FastAPI** backend with webhook-based Telegram bot integration
- **python-telegram-bot** library for bot interactions
- **korail2** library for KTX reservation API calls
- **Subprocess-based worker** system for background reservation attempts

### Key Components

- **src/app.py**: FastAPI server entry point, handles webhook setup and reservation completion callbacks
- **src/telegramBot/bot.py**: Main bot logic with command handlers and conversation flow
- **src/telegramBot/korail_client.py**: Reservation handler that interfaces with Korail API
- **src/telegramBot/worker.py**: Background worker process for automated reservation attempts
- **src/telegramBot/calendar_keyboard.py**: Interactive calendar interface for date selection
- **src/telegramBot/messages.py**: Centralized message templates

### Environment-Specific Behavior

The bot operates in development (`IS_DEV=true`) or production mode:
- **Development**: Port 8390, uses `BOTTOKEN_DEV` and `WEBHOOK_URL_DEV`
- **Production**: Port 8391, uses `BOTTOKEN` and `WEBHOOK_URL`

## Development Commands

### Setup and Installation
```bash
make install          # Install dependencies with pipenv
```

### Running the Application
```bash
make dev              # Development mode (port 8390)
make run              # Production mode (port 8391)
```

### Code Quality
```bash
make lint             # Format code with black
```

### Docker Operations
```bash
make docker-build     # Build Docker image
make docker-run       # Run Docker container
```

## Bot Architecture Details

### State Management
- **userDict**: Stores user conversation state and reservation details
- **runningStatus**: Tracks active reservation processes with PIDs
- **subscribes**: List of users receiving broadcast notifications

### Conversation Flow
1. User authentication (phone number verification against ALLOW_LIST)
2. Korail account login
3. Interactive reservation details collection (date, stations, times, preferences)
4. Background worker spawning for reservation attempts
5. Status updates via HTTP callbacks

### Worker Process
Background reservation attempts run as separate Python processes with:
- Automatic session re-login on errors
- Process monitoring and cleanup
- HTTP callbacks to main bot for status updates

## Environment Variables Required

```bash
USERID           # Default Korail username for admin mode
USERPW           # Default Korail password for admin mode  
BOTTOKEN         # Production Telegram bot token
BOTTOKEN_DEV     # Development Telegram bot token
WEBHOOK_URL      # Production webhook URL
WEBHOOK_URL_DEV  # Development webhook URL
ALLOW_LIST       # Comma-separated phone numbers allowed to use bot
ADMINPW          # Admin password for privileged access
```

## Testing and Quality Assurance

No automated test framework is currently configured. Manual testing should focus on:
- Complete reservation flow end-to-end
- Error handling and recovery scenarios
- Process cleanup and resource management
- Both development and production environment configurations

Always run `make lint` before committing changes to maintain code formatting consistency.