import { extension_settings, getContext } from "../../../extensions.js";
import { saveSettingsDebounced } from "../../../../script.js";
import { chat } from "../../../../script.js";

const extensionName = "SillyTavern-Extension-ChatBridge";
const defaultSettings = {
  wsPort: 8001,
  autoConnect: false,
};

if (!extension_settings[extensionName]) {
  extension_settings[extensionName] = {};
}

Object.assign(extension_settings[extensionName], defaultSettings);

let ws;
let activeBridgeRequest = null;

function updateDebugLog(message) {
  const debugLog = $("#debug_log");
  if (debugLog.length === 0) {
    console.warn("Debug log element not found");
    return;
  }
  const timestamp = new Date().toLocaleTimeString();
  const currentContent = debugLog.val();
  const newLine = `[${timestamp}] ${message}\n`;
  debugLog.val(currentContent + newLine);
  debugLog.scrollTop(debugLog[0].scrollHeight);
  console.log(`[${extensionName}] ${message}`);
}

function updateWSStatus(connected) {
  const status = $("#ws_status");
  if (connected) {
    status.text("Connected").css("color", "green");
  } else {
    status.text("Disconnected").css("color", "red");
  }
}

function convertOpenAIToSTMessage(msg, userName) {
  const isUser = msg.role === "user";
  const currentTime = new Date().toLocaleString();
  // Use: 1) explicitly passed userName, 2) ST's configured name1, 3) fallback 'user'
  const resolvedUserName = userName || getContext().name1 || "user";

  return {
    name: isUser ? resolvedUserName : "Assistant",
    is_user: isUser,
    is_system: false,
    send_date: currentTime,
    mes: msg.content,
    extra: {
      isSmallSys: false,
      token_count: 0,
      reasoning: "",
    },
    force_avatar: isUser ? "User Avatars/default-user.png" : null,
  };
}

function setupWebSocket() {
  const wsUrl = $("#ws_url").val();
  const wsPort = $("#ws_port").val();
  updateDebugLog(`Connecting to WebSocket server: ws://${wsUrl}:${wsPort}`);

  ws = new WebSocket(`ws://${wsUrl}:${wsPort}`);

  ws.onopen = () => {
    updateWSStatus(true);
    updateConnectionButtons(true);
    updateDebugLog("WebSocket connection established");
  };

  ws.onmessage = async (event) => {
    try {
      const data = JSON.parse(event.data);
      updateDebugLog(`Received message: ${JSON.stringify(data)}`);

      if (data.type === "select_character") {
        const name = data.name;
        updateDebugLog(`Selecting character: ${name}`);
        const context = getContext();
        // Search characters array for matching name
        const characters = context.characters;
        if (!characters || characters.length === 0) {
          updateDebugLog(`No characters loaded`);
        } else {
          const idx = characters.findIndex(
            (c) => c.name.trim().toLowerCase() === name.trim().toLowerCase()
          );
          if (idx !== -1) {
            // Use ST's selectCharacterById with the array index
            await context.selectCharacterById(String(idx));
            updateDebugLog(`Character selected: ${name} (index ${idx})`);
          } else {
            updateDebugLog(`Character not found: ${name}`);
            updateDebugLog(`Available: ${characters.map((c) => c.name).join(", ")}`);
          }
        }
      } else if (data.type === "user_request") {
        updateDebugLog("Received user request");
        if (data.content?.messages) {
          activeBridgeRequest = {
            id: data.id,
            content: data.content,
          };
          const context = getContext();
          // Use user name from request if provided, else fall back to ST's name1
          const userName = data.content.user || context.name1 || "user";
          const newChat = data.content.messages
            .filter((msg) => msg.role === "user" || msg.role === "assistant")
            .map((msg) => convertOpenAIToSTMessage(msg, userName));

          chat.splice(0, chat.length, ...newChat);
          context.clearChat();
          context.printMessages();
          context.eventSource.emit(
            context.eventTypes.CHAT_CHANGED,
            context.getCurrentChatId(),
          );
          updateDebugLog(`Chat updated with ${context.chat.length} messages`);
          $("#send_but").click();
        } else {
          updateDebugLog("Error: Incorrect message format");
        }
      }
    } catch (error) {
      updateDebugLog(`Error processing message: ${error.message}`);
      console.error(error);
    }
  };

  ws.onclose = () => {
    updateWSStatus(false);
    updateConnectionButtons(false);
    updateDebugLog("WebSocket connection closed");
  };

  ws.onerror = (error) => {
    updateWSStatus(false);
    updateDebugLog(`WebSocket error: ${error}`);
  };
}

function observeNewMessages() {
  const observer = new MutationObserver((mutations) => {
    for (const mutation of mutations) {
      if (mutation.addedNodes.length > 0) {
        const lastMessage = chat[chat.length - 1];
        // If there's an active request and the last message is from the assistant, send it back
        if (activeBridgeRequest && lastMessage && !lastMessage.is_user) {
          const response = {
            type: "st_response",
            id: activeBridgeRequest.id,
            content: {
              choices: [{
                message: {
                  role: "assistant",
                  content: lastMessage.mes,
                },
              }],
            },
          };
          ws.send(JSON.stringify(response));
          updateDebugLog(`Response sent for request ${activeBridgeRequest.id}`);
          activeBridgeRequest = null;
        }
      }
    }
  });

  const chatElement = document.querySelector("#chat");
  if (chatElement) {
    observer.observe(chatElement, { childList: true, subtree: true });
    updateDebugLog("Message observer initialized");
  } else {
    updateDebugLog("Warning: Could not find #chat element");
  }
}

function updateConnectionButtons(connected) {
  $("#ws_connect").prop("disabled", connected);
  $("#ws_disconnect").prop("disabled", !connected);
  $("#ws_url").prop("disabled", connected);
  $("#ws_port").prop("disabled", connected);
}

function disconnectWebSocket() {
  if (ws) {
    ws.close();
  }
  updateWSStatus(false);
  updateConnectionButtons(false);
  updateDebugLog("WebSocket disconnected");
  if (extension_settings[extensionName].autoConnect) {
    startAutoConnect();
  }
}

// Auto-reconnect logic
let autoConnectTimer = null;

function startAutoConnect() {
  if (autoConnectTimer) {
    clearInterval(autoConnectTimer);
  }
  autoConnectTimer = setInterval(() => {
    if (!ws || ws.readyState === WebSocket.CLOSED) {
      updateDebugLog("Auto-reconnect attempt...");
      setupWebSocket();
    }
  }, 5000);
}

function stopAutoConnect() {
  if (autoConnectTimer) {
    clearInterval(autoConnectTimer);
    autoConnectTimer = null;
  }
}

jQuery(async () => {
  const template = await $.get(
    `/scripts/extensions/third-party/${extensionName}/index.html`,
  );
  $("#extensions_settings").append(template);

  $("#ws_connect").on("click", setupWebSocket);
  $("#ws_disconnect").on("click", disconnectWebSocket);
  $("#ws_port").val(extension_settings[extensionName].wsPort);

  $("#ws_port").on("change", function () {
    extension_settings[extensionName].wsPort = $(this).val();
    saveSettingsDebounced();
  });

  setupWebSocket();
  observeNewMessages();

  // Auto-connect checkbox
  $("#ws_auto_connect").prop(
    "checked",
    extension_settings[extensionName].autoConnect,
  );
  $("#ws_auto_connect").on("change", function () {
    const isChecked = $(this).prop("checked");
    extension_settings[extensionName].autoConnect = isChecked;
    saveSettingsDebounced();

    if (isChecked) {
      updateDebugLog("Auto-reconnect enabled");
      startAutoConnect();
    } else {
      updateDebugLog("Auto-reconnect disabled");
      stopAutoConnect();
    }
  });

  if (extension_settings[extensionName].autoConnect) {
    startAutoConnect();
  }

  updateDebugLog("Extension initialized");
});
