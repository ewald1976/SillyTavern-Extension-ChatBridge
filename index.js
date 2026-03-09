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

function convertOpenAIToSTMessage(msg) {
  const isUser = msg.role === "user";
  const currentTime = new Date().toLocaleString();

  return {
    name: isUser ? "user" : "Assistant",
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

      if (data.type === "user_request") {
        updateDebugLog("Received user request");
        if (data.content?.messages) {
          const context = getContext();
          const newChat = data.content.messages
            .filter((msg) => msg.role === "user" || msg.role === "assistant")
            .map((msg) => convertOpenAIToSTMessage(msg));

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
