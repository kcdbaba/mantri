/**
 * Mantri Baileys monitor process.
 *
 * Connects to WhatsApp using Ashish's number (monitor only — never sends).
 * Forwards every message event to FastAPI /ingest as a POST request.
 *
 * Auth state is persisted to ./baileys_auth/ — QR code printed on first run.
 * Subsequent runs reconnect automatically from saved auth state.
 *
 * Usage:
 *   cd src/ingestion/baileys && npm install && node index.js
 *
 * First run: scan the QR code with Ashish's WhatsApp.
 * On reconnect: session restores automatically.
 */

import makeWASocket, {
  useMultiFileAuthState,
  DisconnectReason,
  fetchLatestBaileysVersion,
  makeCacheableSignalKeyStore,
} from "@whiskeysockets/baileys";
import pino from "pino";
import { Boom } from "@hapi/boom";

const FASTAPI_INGEST_URL = process.env.FASTAPI_URL || "http://localhost:8000/ingest";
const AUTH_DIR = "./baileys_auth";

const logger = pino({ level: "warn" }); // suppress Baileys verbose output

function detectMediaType(msg) {
  const m = msg.message;
  if (!m) return "system";
  if (m.reactionMessage) return "reaction";
  if (m.stickerMessage) return "sticker";
  if (m.imageMessage) return "image";
  if (m.audioMessage || m.pttMessage) return "audio";
  if (m.conversation || m.extendedTextMessage) return "text";
  return "system";
}

function extractBody(msg) {
  const m = msg.message;
  if (!m) return null;
  return (
    m.conversation ||
    m.extendedTextMessage?.text ||
    m.imageMessage?.caption ||
    null
  );
}

function extractMediaUrl(msg) {
  // Baileys provides a download URL for media — short-lived (~10 min)
  const m = msg.message;
  if (!m) return null;
  const mediaMsg = m.imageMessage || m.audioMessage || m.pttMessage;
  return mediaMsg?.url || null;
}

async function sendToFastAPI(payload) {
  try {
    const res = await fetch(FASTAPI_INGEST_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      console.error(`[ingest] HTTP ${res.status} for message ${payload.message_id}`);
    }
  } catch (err) {
    console.error(`[ingest] Failed to POST message ${payload.message_id}:`, err.message);
  }
}

async function startSocket() {
  const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);
  const { version } = await fetchLatestBaileysVersion();

  const sock = makeWASocket({
    version,
    auth: {
      creds: state.creds,
      keys: makeCacheableSignalKeyStore(state.keys, logger),
    },
    logger,
    printQRInTerminal: true,
    getMessage: async () => undefined, // don't fetch history
  });

  sock.ev.on("creds.update", saveCreds);

  sock.ev.on("connection.update", ({ connection, lastDisconnect, qr }) => {
    if (qr) {
      console.log("[baileys] Scan this QR code with WhatsApp:");
    }
    if (connection === "close") {
      const reason = new Boom(lastDisconnect?.error)?.output?.statusCode;
      const shouldReconnect = reason !== DisconnectReason.loggedOut;
      console.log(`[baileys] Connection closed (reason=${reason}). Reconnecting: ${shouldReconnect}`);
      if (shouldReconnect) {
        setTimeout(startSocket, 3000);
      } else {
        console.error("[baileys] Logged out — delete baileys_auth/ and restart to re-auth");
        process.exit(1);
      }
    }
    if (connection === "open") {
      console.log("[baileys] Connected. Monitoring groups...");
    }
  });

  // Print all group JIDs on startup so you can update config.py
  sock.ev.on("messaging-history.set", ({ chats }) => {
    const groups = chats.filter((c) => c.id?.endsWith("@g.us"));
    if (groups.length > 0) {
      console.log("[baileys] Groups visible to this number:");
      groups.forEach((g) => console.log(`  ${g.id}  ${g.name || "(no name)"}`));
      console.log("[baileys] Copy group JIDs above into src/config.py MONITORED_GROUPS");
    }
  });

  sock.ev.on("messages.upsert", async ({ messages, type }) => {
    if (type !== "notify") return;

    for (const msg of messages) {
      // Skip messages from non-group chats (DMs to this number) for now
      if (!msg.key.remoteJid?.endsWith("@g.us")) continue;
      // Skip own messages
      if (msg.key.fromMe) continue;

      const mediaType = detectMediaType(msg);

      const payload = {
        message_id: msg.key.id,
        group_id: msg.key.remoteJid,
        sender_jid: msg.key.participant || msg.key.remoteJid,
        timestamp: msg.messageTimestamp,
        body: extractBody(msg),
        media_type: mediaType,
        media_url: extractMediaUrl(msg),
      };

      console.log(`[baileys] ${payload.group_id} | ${mediaType} | ${payload.body?.slice(0, 60) ?? ""}`);
      await sendToFastAPI(payload);
    }
  });
}

startSocket().catch((err) => {
  console.error("[baileys] Fatal error:", err);
  process.exit(1);
});
