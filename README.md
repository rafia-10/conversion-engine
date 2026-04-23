# Conversion Engine

## Overview
The Conversion Engine is a Python-based project designed to process and enrich data using various APIs and services. It includes an agent package for handling specific tasks like email, SMS, CRM, and calendar integrations.

## Features
- Integration with OpenRouter's DeepSeek model for LLM functionalities.
- Support for external APIs like Resend, Africa's Talking, and HubSpot.
- Dockerized setup for easy deployment.

## Setup

### Prerequisites
- Docker and Docker Compose installed.
- Python 3.8 or higher.

### Installation
1. Clone the repository:
   ```bash
   git clone <repository-url>
   cd conversion-engine
   ```
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   pip install -r agent/requirements.txt
   ```

### Running the Application
1. Start the services using Docker Compose:
   ```bash
   docker-compose up
   ```
2. Access the application at `http://localhost:5000`.

## Directory Structure
- `agent/`: Contains the agent package for handling specific tasks.
- `docker-compose.yml`: Docker Compose configuration file.
- `requirements.txt`: Main project dependencies.
- `agent/requirements.txt`: Agent-specific dependencies.

## Contributing
Feel free to submit issues and pull requests for improvements.

## License
This project is licensed under the MIT License.