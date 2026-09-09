# SATVA dashboard — Vite dev server.
#
# Development image: docker-compose.yml bind-mounts ./dashboard over /app and
# keeps /app/node_modules as an anonymous volume, so the dependency tree built
# here is what the container actually runs. See .dockerignore — without it the
# COPY below would overwrite that tree with the host's.
FROM node:20-alpine

WORKDIR /app

# Dependencies first so a source edit does not invalidate the install layer.
COPY package.json package-lock.json ./

RUN npm ci

COPY . .

# Run as a non-root user, matching backend/Dockerfile: the dev server writes its
# transform cache into node_modules/.vite, and a container that writes files
# should not be doing so as root. node:20-alpine ships an unprivileged `node`.
RUN chown -R node:node /app
USER node

# Host and port come from vite.config.js (server.host / server.port) so there is
# one source of truth; do not repeat them here or in the compose command.
EXPOSE 5173

CMD ["npm", "run", "dev"]
