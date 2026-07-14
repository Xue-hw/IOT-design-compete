/*
 * SPDX-FileCopyrightText: 2025 Espressif Systems (Shanghai) CO LTD
 *
 * SPDX-License-Identifier: Apache-2.0
 */
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include <strings.h>

#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/task.h"
#include "nvs_flash.h"
#include "esp_log.h"
#include "esp_err.h"
#include "esp_check.h"
#include "esp_event.h"
#include "esp_http_client.h"
#include "esp_netif.h"
#include "esp_system.h"
#include "esp_wifi.h"
#include "cJSON.h"
#include "esp_memory_utils.h"
#include "lvgl.h"
#include "bsp/esp-bsp.h"
#include "bsp/display.h"
#include "bsp_board_extra.h"

LV_FONT_DECLARE(focuscube_font_cn_18);

static const char *TAG = "focuscube_p4";

#define FOCUSCUBE_WIFI_CONNECTED_BIT BIT0
#define FOCUSCUBE_WIFI_FAIL_BIT      BIT1
#define FOCUSCUBE_HTTP_BODY_MAX      4096
#define FOCUSCUBE_STATUS_INTERVAL_MS 3000
#define FOCUSCUBE_REMINDER_INTERVAL_MS 10000
#define FOCUSCUBE_REPORT_INTERVAL_MS 60000
#define FOCUSCUBE_PORTAL_RECHECK_MS 45000

typedef struct {
    bool has_s3_online;
    bool s3_online;
    int lux;
    int focus_minutes;
    int pomodoro_count;
    int remaining_seconds;
    int battery_pct;
    bool charging;
    char focus_state[24];
    char light_label[24];
    char s3_summary[128];
    char report[512];
    char suggestion[256];
    char reminders[512];
} focuscube_data_t;

typedef struct {
    char *data;
    size_t len;
    size_t cap;
    char location[512];
    char cookie[256];
} http_response_buffer_t;

static EventGroupHandle_t s_wifi_event_group;
static int s_wifi_retry_count;
static volatile bool s_wifi_has_ip;
static volatile bool s_backend_live;
static uint32_t s_api_success_count;

static lv_obj_t *s_badge_obj;
static lv_obj_t *s_badge_label;
static lv_obj_t *s_s3_state_label;
static lv_obj_t *s_lux_label;
static lv_obj_t *s_focus_label;
static lv_obj_t *s_battery_label;
static lv_obj_t *s_report_label;
static lv_obj_t *s_reminder_label;
static lv_obj_t *s_footer_label;

static lv_obj_t *create_text(lv_obj_t *parent, const char *text, int32_t x, int32_t y,
                             int32_t w, const lv_font_t *font, lv_color_t color)
{
    lv_obj_t *label = lv_label_create(parent);
    lv_label_set_text(label, text);
    lv_label_set_long_mode(label, LV_LABEL_LONG_WRAP);
    lv_obj_set_width(label, w);
    lv_obj_set_pos(label, x, y);
    lv_obj_set_style_text_font(label, font, 0);
    lv_obj_set_style_text_color(label, color, 0);
    return label;
}

static lv_obj_t *create_card(lv_obj_t *parent, int32_t x, int32_t y, int32_t w, int32_t h,
                             const char *title, lv_color_t accent)
{
    lv_obj_t *card = lv_obj_create(parent);
    lv_obj_set_pos(card, x, y);
    lv_obj_set_size(card, w, h);
    lv_obj_set_style_radius(card, 14, 0);
    lv_obj_set_style_bg_color(card, lv_color_hex(0xffffff), 0);
    lv_obj_set_style_border_width(card, 0, 0);
    lv_obj_set_style_shadow_width(card, 16, 0);
    lv_obj_set_style_shadow_opa(card, LV_OPA_20, 0);
    lv_obj_set_style_pad_all(card, 0, 0);
    lv_obj_clear_flag(card, LV_OBJ_FLAG_SCROLLABLE);

    lv_obj_t *bar = lv_obj_create(card);
    lv_obj_set_pos(bar, 0, 0);
    lv_obj_set_size(bar, 8, h);
    lv_obj_set_style_radius(bar, 0, 0);
    lv_obj_set_style_bg_color(bar, accent, 0);
    lv_obj_set_style_border_width(bar, 0, 0);
    lv_obj_clear_flag(bar, LV_OBJ_FLAG_SCROLLABLE);

    create_text(card, title, 24, 18, w - 48, &focuscube_font_cn_18, lv_color_hex(0x5c6470));
    return card;
}

static bool focuscube_wifi_configured(void)
{
    return strlen(CONFIG_FOCUSCUBE_WIFI_SSID) > 0;
}

static bool focuscube_backend_configured(void)
{
    return strlen(CONFIG_FOCUSCUBE_BACKEND_BASE_URL) > 0;
}

static void focuscube_data_init(focuscube_data_t *data)
{
    memset(data, 0, sizeof(*data));
    data->lux = -1;
    data->focus_minutes = -1;
    data->pomodoro_count = -1;
    data->remaining_seconds = -1;
    data->battery_pct = -1;
    snprintf(data->light_label, sizeof(data->light_label), "%s", "--");
    snprintf(data->s3_summary, sizeof(data->s3_summary), "%s", "等待状态数据");
    snprintf(data->report, sizeof(data->report), "%s", "等待 AI 复盘数据");
    snprintf(data->suggestion, sizeof(data->suggestion), "%s", "等待建议");
    snprintf(data->reminders, sizeof(data->reminders), "%s", "等待提醒数据");
}

static bool copy_json_string(char *dst, size_t dst_size, cJSON *obj, const char *key)
{
    cJSON *item = cJSON_GetObjectItemCaseSensitive(obj, key);

    if (cJSON_IsString(item) && item->valuestring != NULL) {
        snprintf(dst, dst_size, "%s", item->valuestring);
        return true;
    }

    return false;
}

static bool get_json_int(cJSON *obj, const char *key, int *out)
{
    cJSON *item = cJSON_GetObjectItemCaseSensitive(obj, key);

    if (!cJSON_IsNumber(item)) {
        return false;
    }

    *out = (int)item->valuedouble;
    return true;
}

static void dashboard_set_badge(const char *text, lv_color_t color)
{
    if (s_badge_obj != NULL) {
        lv_obj_set_style_bg_color(s_badge_obj, color, 0);
    }

    if (s_badge_label != NULL) {
        lv_label_set_text(s_badge_label, text);
    }
}

static void dashboard_apply_live_data(const focuscube_data_t *data)
{
    bool first_live_frame = !s_backend_live;
    s_backend_live = true;
    s_api_success_count++;

    dashboard_set_badge("LIVE API", lv_color_hex(0x1f9d55));

    if (s_s3_state_label != NULL) {
        if (data->has_s3_online) {
            lv_label_set_text(s_s3_state_label, data->s3_online ? "S3：在线" : "S3：离线");
        } else {
            lv_label_set_text(s_s3_state_label, data->s3_summary);
        }
    }

    if (s_lux_label != NULL) {
        if (data->lux >= 0) {
            lv_label_set_text_fmt(s_lux_label, "当前光照：%d lux · %s", data->lux, data->light_label);
        } else {
            lv_label_set_text(s_lux_label, "当前光照：等待数据");
        }
    }

    if (s_focus_label != NULL) {
        if (strcmp(data->focus_state, "running") == 0 && data->remaining_seconds >= 0) {
            lv_label_set_text_fmt(s_focus_label, "专注状态：专注中 · 剩余 %d 分钟 · 第 %d 次",
                                  (data->remaining_seconds + 59) / 60,
                                  data->pomodoro_count >= 0 ? data->pomodoro_count : 0);
        } else if (strcmp(data->focus_state, "paused") == 0) {
            lv_label_set_text(s_focus_label, "专注状态：已暂停");
        } else if (strcmp(data->focus_state, "idle") == 0) {
            lv_label_set_text(s_focus_label, "专注状态：空闲");
        } else if (data->focus_minutes >= 0 && data->pomodoro_count >= 0) {
            lv_label_set_text_fmt(s_focus_label, "今日专注：%d 分钟 · %d 个周期",
                                  data->focus_minutes, data->pomodoro_count);
        } else if (data->focus_minutes >= 0) {
            lv_label_set_text_fmt(s_focus_label, "今日专注：%d 分钟", data->focus_minutes);
        } else {
            lv_label_set_text(s_focus_label, "专注状态：等待数据");
        }
    }

    if (s_battery_label != NULL) {
        if (data->battery_pct >= 0) {
            lv_label_set_text_fmt(s_battery_label, "电量：%d%% · %s",
                                  data->battery_pct, data->charging ? "正在充电" : "未充电");
        } else {
            lv_label_set_text(s_battery_label, "电量：等待数据");
        }
    }

    if (s_report_label != NULL) {
        lv_label_set_text_fmt(s_report_label, "%s\n\n建议：%s", data->report, data->suggestion);
    }

    if (s_reminder_label != NULL) {
        lv_label_set_text(s_reminder_label, data->reminders);
    }

    if (s_footer_label != NULL) {
        lv_label_set_text_fmt(s_footer_label, "WiFi connected - backend %s - live refresh %lu",
                              CONFIG_FOCUSCUBE_BACKEND_BASE_URL, (unsigned long)s_api_success_count);
    }

    /* Static card titles are drawn only once. Redraw the full dashboard after
     * networking settles so compressed CJK glyphs cannot retain a partial
     * first frame from display startup. */
    if (first_live_frame) {
        lv_obj_invalidate(lv_scr_act());
    }
}

static void dashboard_set_network_note(const char *badge, lv_color_t color, const char *note)
{
    dashboard_set_badge(badge, color);

    if (s_footer_label != NULL) {
        lv_label_set_text(s_footer_label, note);
    }
}

static void dashboard_apply_live_data_from_task(const focuscube_data_t *data)
{
    if (bsp_display_lock(1000)) {
        dashboard_apply_live_data(data);
        bsp_display_unlock();
    }
}

static void dashboard_set_network_note_from_task(const char *badge, lv_color_t color, const char *note)
{
    if (bsp_display_lock(1000)) {
        dashboard_set_network_note(badge, color, note);
        bsp_display_unlock();
    }
}

static esp_err_t focuscube_nvs_init(void)
{
    esp_err_t ret = nvs_flash_init();

    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_RETURN_ON_ERROR(nvs_flash_erase(), TAG, "erase nvs");
        ret = nvs_flash_init();
    }

    return ret;
}

static void focuscube_wifi_event_handler(void *arg, esp_event_base_t event_base,
                                         int32_t event_id, void *event_data)
{
    (void)arg;

    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        s_wifi_has_ip = false;

        if (s_wifi_retry_count < CONFIG_FOCUSCUBE_WIFI_MAXIMUM_RETRY) {
            esp_wifi_connect();
            s_wifi_retry_count++;
            ESP_LOGI(TAG, "retry WiFi connection");
        } else if (s_wifi_event_group != NULL) {
            xEventGroupSetBits(s_wifi_event_group, FOCUSCUBE_WIFI_FAIL_BIT);
        }
    } else if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t *event = (ip_event_got_ip_t *)event_data;
        ESP_LOGI(TAG, "got ip:" IPSTR, IP2STR(&event->ip_info.ip));
        s_wifi_retry_count = 0;
        s_wifi_has_ip = true;

        if (s_wifi_event_group != NULL) {
            xEventGroupSetBits(s_wifi_event_group, FOCUSCUBE_WIFI_CONNECTED_BIT);
        }
    }
}

static esp_err_t focuscube_wifi_init_sta(void)
{
    s_wifi_event_group = xEventGroupCreate();

    if (s_wifi_event_group == NULL) {
        return ESP_ERR_NO_MEM;
    }

    ESP_RETURN_ON_ERROR(esp_netif_init(), TAG, "init netif");

    esp_err_t ret = esp_event_loop_create_default();
    if (ret != ESP_OK && ret != ESP_ERR_INVALID_STATE) {
        return ret;
    }

    esp_netif_create_default_wifi_sta();

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_RETURN_ON_ERROR(esp_wifi_init(&cfg), TAG, "init wifi");
    ESP_RETURN_ON_ERROR(esp_event_handler_instance_register(WIFI_EVENT, ESP_EVENT_ANY_ID,
                                                            &focuscube_wifi_event_handler, NULL, NULL),
                        TAG, "register wifi event");
    ESP_RETURN_ON_ERROR(esp_event_handler_instance_register(IP_EVENT, IP_EVENT_STA_GOT_IP,
                                                            &focuscube_wifi_event_handler, NULL, NULL),
                        TAG, "register ip event");

    wifi_config_t wifi_config = { 0 };
    snprintf((char *)wifi_config.sta.ssid, sizeof(wifi_config.sta.ssid), "%s", CONFIG_FOCUSCUBE_WIFI_SSID);
    snprintf((char *)wifi_config.sta.password, sizeof(wifi_config.sta.password), "%s", CONFIG_FOCUSCUBE_WIFI_PASSWORD);
    wifi_config.sta.threshold.authmode = strlen(CONFIG_FOCUSCUBE_WIFI_PASSWORD) == 0 ? WIFI_AUTH_OPEN : WIFI_AUTH_WPA_PSK;

    ESP_RETURN_ON_ERROR(esp_wifi_set_mode(WIFI_MODE_STA), TAG, "set wifi mode");
    ESP_RETURN_ON_ERROR(esp_wifi_set_config(WIFI_IF_STA, &wifi_config), TAG, "set wifi config");
    ESP_RETURN_ON_ERROR(esp_wifi_start(), TAG, "start wifi");

    EventBits_t bits = xEventGroupWaitBits(s_wifi_event_group,
                                           FOCUSCUBE_WIFI_CONNECTED_BIT | FOCUSCUBE_WIFI_FAIL_BIT,
                                           pdFALSE,
                                           pdFALSE,
                                           pdMS_TO_TICKS(15000));

    if (bits & FOCUSCUBE_WIFI_CONNECTED_BIT) {
        ESP_LOGI(TAG, "connected to WiFi SSID:%s", CONFIG_FOCUSCUBE_WIFI_SSID);
        return ESP_OK;
    }

    ESP_LOGW(TAG, "WiFi connection timeout or failed");
    return ESP_ERR_TIMEOUT;
}

static esp_err_t focuscube_http_event_handler(esp_http_client_event_t *evt)
{
    http_response_buffer_t *response = (http_response_buffer_t *)evt->user_data;

    if (response == NULL) {
        return ESP_OK;
    }

    if (evt->event_id == HTTP_EVENT_ON_HEADER && evt->header_key != NULL && evt->header_value != NULL) {
        if (strcasecmp(evt->header_key, "Location") == 0) {
            snprintf(response->location, sizeof(response->location), "%s", evt->header_value);
        } else if (strcasecmp(evt->header_key, "Set-Cookie") == 0 && response->cookie[0] == '\0') {
            snprintf(response->cookie, sizeof(response->cookie), "%s", evt->header_value);
        }
    } else if (evt->event_id == HTTP_EVENT_ON_DATA && evt->data_len > 0 &&
               response->data != NULL && response->cap > 0) {
        size_t free_len = response->cap - response->len - 1;
        size_t copy_len = evt->data_len < free_len ? evt->data_len : free_len;

        if (copy_len > 0) {
            memcpy(response->data + response->len, evt->data, copy_len);
            response->len += copy_len;
            response->data[response->len] = '\0';
        }
    }

    return ESP_OK;
}

#if CONFIG_FOCUSCUBE_BUPT_PORTAL_ENABLE
static bool focuscube_url_encode(const char *input, char *output, size_t output_size)
{
    static const char hex[] = "0123456789ABCDEF";
    size_t used = 0;

    for (const unsigned char *p = (const unsigned char *)input; *p != '\0'; p++) {
        bool plain = (*p >= 'a' && *p <= 'z') || (*p >= 'A' && *p <= 'Z') ||
                     (*p >= '0' && *p <= '9') || *p == '-' || *p == '_' ||
                     *p == '.' || *p == '~';
        size_t needed = plain ? 1 : 3;
        if (used + needed + 1 > output_size) {
            return false;
        }

        if (plain) {
            output[used++] = (char)*p;
        } else {
            output[used++] = '%';
            output[used++] = hex[*p >> 4];
            output[used++] = hex[*p & 0x0f];
        }
    }

    output[used] = '\0';
    return true;
}

static bool focuscube_portal_request(const char *url, esp_http_client_method_t method,
                                     const char *post_data, const char *cookie,
                                     http_response_buffer_t *response, int *status_code)
{
    esp_http_client_config_t config = {
        .url = url,
        .timeout_ms = 8000,
        .event_handler = focuscube_http_event_handler,
        .user_data = response,
        .buffer_size = 1024,
        .disable_auto_redirect = true,
    };
    esp_http_client_handle_t client = esp_http_client_init(&config);
    if (client == NULL) {
        return false;
    }

    esp_http_client_set_method(client, method);
    esp_http_client_set_header(client, "User-Agent",
                               "Mozilla/5.0 FocusCube-P4 ESP-IDF/6.0.2");
    if (cookie != NULL && cookie[0] != '\0') {
        esp_http_client_set_header(client, "Cookie", cookie);
    }
    if (post_data != NULL) {
        esp_http_client_set_header(client, "Content-Type", "application/x-www-form-urlencoded");
        esp_http_client_set_post_field(client, post_data, strlen(post_data));
    }

    esp_err_t err = esp_http_client_perform(client);
    *status_code = esp_http_client_get_status_code(client);
    esp_http_client_cleanup(client);
    return err == ESP_OK;
}

static bool focuscube_bupt_portal_login(void)
{
    if (CONFIG_FOCUSCUBE_BUPT_ACCOUNT[0] == '\0' || CONFIG_FOCUSCUBE_BUPT_PASSWORD[0] == '\0') {
        ESP_LOGW(TAG, "BUPT portal credentials are not configured");
        return false;
    }

    char probe_body[384] = {0};
    http_response_buffer_t probe = {
        .data = probe_body,
        .cap = sizeof(probe_body),
    };
    int status = 0;
    if (!focuscube_portal_request(CONFIG_FOCUSCUBE_BUPT_PROBE_URL, HTTP_METHOD_GET,
                                  NULL, NULL, &probe, &status)) {
        ESP_LOGW(TAG, "BUPT portal probe request failed");
        return false;
    }

    if (probe.location[0] == '\0' && status >= 200 && status < 300) {
        ESP_LOGI(TAG, "BUPT portal is already authenticated");
        return true;
    }
    if (probe.location[0] == '\0') {
        ESP_LOGW(TAG, "BUPT portal redirect missing, status=%d", status);
        return false;
    }

    char portal_body[512] = {0};
    http_response_buffer_t portal = {
        .data = portal_body,
        .cap = sizeof(portal_body),
    };
    if (!focuscube_portal_request(probe.location, HTTP_METHOD_GET, NULL, NULL, &portal, &status)) {
        ESP_LOGW(TAG, "BUPT portal landing request failed");
        return false;
    }
    if (portal.cookie[0] == '\0') {
        ESP_LOGW(TAG, "BUPT portal cookie missing");
        return false;
    }

    char *semicolon = strchr(portal.cookie, ';');
    if (semicolon != NULL) {
        *semicolon = '\0';
    }

    char account[192];
    char password[192];
    char post_data[448];
    if (!focuscube_url_encode(CONFIG_FOCUSCUBE_BUPT_ACCOUNT, account, sizeof(account)) ||
        !focuscube_url_encode(CONFIG_FOCUSCUBE_BUPT_PASSWORD, password, sizeof(password))) {
        ESP_LOGE(TAG, "BUPT portal credential is too long");
        return false;
    }
    snprintf(post_data, sizeof(post_data), "user=%s&pass=%s", account, password);

    char login_body[512] = {0};
    http_response_buffer_t login = {
        .data = login_body,
        .cap = sizeof(login_body),
    };
    if (!focuscube_portal_request(CONFIG_FOCUSCUBE_BUPT_LOGIN_URL, HTTP_METHOD_POST,
                                  post_data, portal.cookie, &login, &status)) {
        ESP_LOGW(TAG, "BUPT portal login POST failed");
        return false;
    }

    memset(&probe, 0, sizeof(probe));
    probe.data = probe_body;
    probe.cap = sizeof(probe_body);
    probe_body[0] = '\0';
    if (!focuscube_portal_request(CONFIG_FOCUSCUBE_BUPT_PROBE_URL, HTTP_METHOD_GET,
                                  NULL, NULL, &probe, &status)) {
        return false;
    }

    bool authenticated = probe.location[0] == '\0' && status >= 200 && status < 300;
    ESP_LOGI(TAG, "BUPT portal authentication %s (status=%d)",
             authenticated ? "succeeded" : "failed", status);
    return authenticated;
}
#endif

static bool focuscube_build_url(const char *path, char *url, size_t url_size)
{
    const char *base = CONFIG_FOCUSCUBE_BACKEND_BASE_URL;
    size_t base_len = strlen(base);

    if (base_len == 0 || path == NULL || path[0] == '\0') {
        return false;
    }

    if (base[base_len - 1] == '/' && path[0] == '/') {
        snprintf(url, url_size, "%.*s%s", (int)(base_len - 1), base, path);
    } else if (base[base_len - 1] != '/' && path[0] != '/') {
        snprintf(url, url_size, "%s/%s", base, path);
    } else {
        snprintf(url, url_size, "%s%s", base, path);
    }

    return true;
}

static bool focuscube_http_get(const char *path, char *body, size_t body_size)
{
    char url[256];

    if (!focuscube_build_url(path, url, sizeof(url))) {
        return false;
    }

    body[0] = '\0';
    http_response_buffer_t response = {
        .data = body,
        .len = 0,
        .cap = body_size,
    };
    esp_http_client_config_t config = {
        .url = url,
        .timeout_ms = CONFIG_FOCUSCUBE_HTTP_TIMEOUT_MS,
        .event_handler = focuscube_http_event_handler,
        .user_data = &response,
        .buffer_size = 1024,
    };

    esp_http_client_handle_t client = esp_http_client_init(&config);
    if (client == NULL) {
        return false;
    }

    esp_http_client_set_method(client, HTTP_METHOD_GET);
    esp_http_client_set_header(client, "Accept", "application/json");

    esp_err_t err = esp_http_client_perform(client);
    int status_code = esp_http_client_get_status_code(client);
    esp_http_client_cleanup(client);

    if (err != ESP_OK || status_code < 200 || status_code >= 300 || response.len == 0) {
        ESP_LOGW(TAG, "GET %s failed: err=%s status=%d len=%u",
                 path, esp_err_to_name(err), status_code, (unsigned int)response.len);
        return false;
    }

    return true;
}

static bool focuscube_parse_status(const char *json, focuscube_data_t *data)
{
    cJSON *root = cJSON_Parse(json);
    if (root == NULL) {
        return false;
    }

    bool parsed = false;
    cJSON *devices = cJSON_GetObjectItemCaseSensitive(root, "devices");

    if (cJSON_IsArray(devices)) {
        cJSON *device = NULL;

        cJSON_ArrayForEach(device, devices) {
            cJSON *device_id = cJSON_GetObjectItemCaseSensitive(device, "device_id");
            cJSON *source = cJSON_GetObjectItemCaseSensitive(device, "source");
            bool is_s3 = (cJSON_IsString(device_id) && strcmp(device_id->valuestring, CONFIG_FOCUSCUBE_DEVICE_ID) == 0) ||
                         (cJSON_IsString(source) && strcmp(source->valuestring, "s3") == 0);

            if (!is_s3) {
                continue;
            }

            cJSON *online = cJSON_GetObjectItemCaseSensitive(device, "online");
            data->has_s3_online = cJSON_IsBool(online);
            data->s3_online = cJSON_IsTrue(online);
            copy_json_string(data->s3_summary, sizeof(data->s3_summary), device, "summary");

            cJSON *light = cJSON_GetObjectItemCaseSensitive(device, "light");
            if (cJSON_IsObject(light)) {
                cJSON *lux = cJSON_GetObjectItemCaseSensitive(light, "lux");
                if (cJSON_IsNumber(lux)) {
                    data->lux = (int)(lux->valuedouble + 0.5);
                }

                cJSON *label = cJSON_GetObjectItemCaseSensitive(light, "label");
                if (cJSON_IsString(label) && label->valuestring != NULL) {
                    const char *display_label = label->valuestring;
                    if (strcmp(display_label, "too_dim") == 0) {
                        display_label = "偏暗";
                    } else if (strcmp(display_label, "too_bright") == 0) {
                        display_label = "偏亮";
                    } else if (strcmp(display_label, "suitable") == 0) {
                        display_label = "适宜";
                    }
                    snprintf(data->light_label, sizeof(data->light_label), "%s", display_label);
                }
            }

            cJSON *focus = cJSON_GetObjectItemCaseSensitive(device, "focus");
            if (cJSON_IsObject(focus)) {
                copy_json_string(data->focus_state, sizeof(data->focus_state), focus, "state");
                get_json_int(focus, "remaining_s", &data->remaining_seconds);
                get_json_int(focus, "session_count", &data->pomodoro_count);
            }

            cJSON *power = cJSON_GetObjectItemCaseSensitive(device, "power");
            if (cJSON_IsObject(power)) {
                get_json_int(power, "battery_pct", &data->battery_pct);
                cJSON *charging = cJSON_GetObjectItemCaseSensitive(power, "charging");
                data->charging = cJSON_IsTrue(charging);
            }

            parsed = true;
            break;
        }
    }

    cJSON_Delete(root);
    return parsed;
}

static bool focuscube_parse_report(const char *json, focuscube_data_t *data)
{
    cJSON *root = cJSON_Parse(json);
    if (root == NULL) {
        return false;
    }

    bool parsed = copy_json_string(data->report, sizeof(data->report), root, "report_text");

    cJSON *suggestions = cJSON_GetObjectItemCaseSensitive(root, "suggestions");
    if (cJSON_IsArray(suggestions) && cJSON_GetArraySize(suggestions) > 0) {
        cJSON *first = cJSON_GetArrayItem(suggestions, 0);
        if (cJSON_IsString(first) && first->valuestring != NULL) {
            snprintf(data->suggestion, sizeof(data->suggestion), "%s", first->valuestring);
            parsed = true;
        }
    }

    cJSON *metrics = cJSON_GetObjectItemCaseSensitive(root, "metrics");
    if (cJSON_IsObject(metrics)) {
        int value;

        if (data->lux < 0 && get_json_int(metrics, "avg_lux", &value)) {
            data->lux = value;
            snprintf(data->light_label, sizeof(data->light_label), "%s", "avg");
            parsed = true;
        }

        if (get_json_int(metrics, "focus_minutes", &value)) {
            data->focus_minutes = value;
            parsed = true;
        }

        if (get_json_int(metrics, "pomodoro_count", &value)) {
            data->pomodoro_count = value;
            parsed = true;
        }
    }

    cJSON_Delete(root);
    return parsed;
}

static bool focuscube_parse_reminders(const char *json, focuscube_data_t *data)
{
    cJSON *root = cJSON_Parse(json);
    if (root == NULL) {
        return false;
    }

    cJSON *items = root;
    if (!cJSON_IsArray(items)) {
        items = cJSON_GetObjectItemCaseSensitive(root, "reminders");
    }
    if (!cJSON_IsArray(items)) {
        items = cJSON_GetObjectItemCaseSensitive(root, "items");
    }

    bool parsed = false;
    if (cJSON_IsArray(items)) {
        struct {
            int priority;
            char text[160];
        } top[3] = {
            {.priority = -1},
            {.priority = -1},
            {.priority = -1},
        };

        cJSON *item = NULL;
        cJSON_ArrayForEach(item, items) {
            cJSON *text = cJSON_GetObjectItemCaseSensitive(item, "text");
            cJSON *priority = cJSON_GetObjectItemCaseSensitive(item, "priority");
            if (!cJSON_IsString(text) || text->valuestring == NULL) {
                continue;
            }

            int value = cJSON_IsNumber(priority) ? (int)priority->valuedouble : 0;
            int insert_at = -1;
            for (int i = 0; i < 3; i++) {
                if (value > top[i].priority) {
                    insert_at = i;
                    break;
                }
            }
            if (insert_at < 0) {
                continue;
            }

            for (int i = 2; i > insert_at; i--) {
                top[i] = top[i - 1];
            }
            top[insert_at].priority = value;
            snprintf(top[insert_at].text, sizeof(top[insert_at].text), "%s", text->valuestring);
        }

        data->reminders[0] = '\0';
        size_t used = 0;
        int shown = 0;
        for (int i = 0; i < 3 && top[i].priority >= 0; i++) {
            int written = snprintf(data->reminders + used, sizeof(data->reminders) - used,
                                   "%d. [P%d] %s%s", i + 1, top[i].priority, top[i].text,
                                   i < 2 && top[i + 1].priority >= 0 ? "\n" : "");
            if (written < 0 || (size_t)written >= sizeof(data->reminders) - used) {
                break;
            }
            used += (size_t)written;
            shown++;
        }

        if (shown == 0) {
            snprintf(data->reminders, sizeof(data->reminders), "%s", "暂无提醒");
        }
        parsed = true;
    }

    cJSON_Delete(root);
    return parsed;
}

static bool focuscube_fetch_status(focuscube_data_t *data)
{
    char body[FOCUSCUBE_HTTP_BODY_MAX];
    return focuscube_http_get("/api/v1/status", body, sizeof(body)) &&
           focuscube_parse_status(body, data);
}

static bool focuscube_fetch_report(focuscube_data_t *data)
{
    char body[FOCUSCUBE_HTTP_BODY_MAX];
    char path[192];
    snprintf(path, sizeof(path), "/api/v1/report/daily?device_id=%s&date=%s",
             CONFIG_FOCUSCUBE_DEVICE_ID, CONFIG_FOCUSCUBE_REPORT_DATE);
    return focuscube_http_get(path, body, sizeof(body)) &&
           focuscube_parse_report(body, data);
}

static bool focuscube_fetch_reminders(focuscube_data_t *data)
{
    char body[FOCUSCUBE_HTTP_BODY_MAX];
    char path[160];
    snprintf(path, sizeof(path), "/api/v1/reminders?device_id=%s&since=0",
             CONFIG_FOCUSCUBE_DEVICE_ID);
    return focuscube_http_get(path, body, sizeof(body)) &&
           focuscube_parse_reminders(body, data);
}

static void focuscube_network_task(void *arg)
{
    (void)arg;

    if (!focuscube_wifi_configured()) {
        dashboard_set_network_note_from_task("LOCAL DEMO", lv_color_hex(0x64748b),
                                             "WiFi not configured. Set FocusCube P4 Configuration to enable HTTP.");
        vTaskDelete(NULL);
        return;
    }

    esp_err_t ret = focuscube_nvs_init();
    if (ret != ESP_OK) {
        dashboard_set_network_note_from_task("NVS ERROR", lv_color_hex(0xb91c1c), esp_err_to_name(ret));
        vTaskDelete(NULL);
        return;
    }

    dashboard_set_network_note_from_task("WIFI...", lv_color_hex(0x2f80ed), "Connecting WiFi...");

    ret = focuscube_wifi_init_sta();
    if (ret != ESP_OK) {
        dashboard_set_network_note_from_task("WIFI FAIL", lv_color_hex(0xb91c1c),
                                             "网络连接异常，正在重试");
    }

    if (!focuscube_backend_configured()) {
        dashboard_set_network_note_from_task("NO API", lv_color_hex(0xf59e0b),
                                             "WiFi connected, but backend URL is not configured.");
        vTaskDelete(NULL);
        return;
    }

    focuscube_data_t data;
    focuscube_data_init(&data);
    TickType_t next_status = 0;
    TickType_t next_reminders = 0;
    TickType_t next_report = 0;
    TickType_t next_wifi_retry = 0;
    bool has_connected_once = false;
#if CONFIG_FOCUSCUBE_BUPT_PORTAL_ENABLE
    TickType_t next_portal_check = 0;
    bool portal_ready = false;
#endif

    while (true) {
        TickType_t now = xTaskGetTickCount();

        if (!s_wifi_has_ip) {
#if CONFIG_FOCUSCUBE_BUPT_PORTAL_ENABLE
            portal_ready = false;
#endif
            dashboard_set_network_note_from_task("WIFI...", lv_color_hex(0xf59e0b),
                                                 has_connected_once
                                                     ? "网络连接异常，正在重试"
                                                     : "正在连接 BUPT-portal 和后端...");
            if ((int32_t)(now - next_wifi_retry) >= 0) {
                s_wifi_retry_count = 0;
                esp_wifi_connect();
                next_wifi_retry = now + pdMS_TO_TICKS(5000);
            }
            vTaskDelay(pdMS_TO_TICKS(500));
            continue;
        }

        has_connected_once = true;

#if CONFIG_FOCUSCUBE_BUPT_PORTAL_ENABLE
        if (CONFIG_FOCUSCUBE_BUPT_ACCOUNT[0] == '\0' || CONFIG_FOCUSCUBE_BUPT_PASSWORD[0] == '\0') {
            dashboard_set_network_note_from_task("NEED ACCOUNT", lv_color_hex(0xf59e0b),
                                                 "已连接 BUPT-portal，请在本机配置校园网账号密码");
            vTaskDelay(pdMS_TO_TICKS(1000));
            continue;
        }

        if (!portal_ready || (int32_t)(now - next_portal_check) >= 0) {
            dashboard_set_network_note_from_task("PORTAL...", lv_color_hex(0x2f80ed),
                                                 "BUPT-portal 校园网认证中...");
            portal_ready = focuscube_bupt_portal_login();
            next_portal_check = now + pdMS_TO_TICKS(portal_ready ? FOCUSCUBE_PORTAL_RECHECK_MS : 10000);
            if (!portal_ready) {
                dashboard_set_network_note_from_task("AUTH RETRY", lv_color_hex(0xf59e0b),
                                                     "校园网认证失败，正在重试");
                vTaskDelay(pdMS_TO_TICKS(1000));
                continue;
            }
        }
#endif

        bool attempted = false;
        bool any_success = false;
        bool any_failure = false;

        if ((int32_t)(now - next_status) >= 0) {
            attempted = true;
            bool ok = focuscube_fetch_status(&data);
            any_success |= ok;
            any_failure |= !ok;
            next_status = now + pdMS_TO_TICKS(FOCUSCUBE_STATUS_INTERVAL_MS);
        }

        if ((int32_t)(now - next_reminders) >= 0) {
            attempted = true;
            bool ok = focuscube_fetch_reminders(&data);
            any_success |= ok;
            any_failure |= !ok;
            next_reminders = now + pdMS_TO_TICKS(FOCUSCUBE_REMINDER_INTERVAL_MS);
        }

        if ((int32_t)(now - next_report) >= 0) {
            attempted = true;
            bool ok = focuscube_fetch_report(&data);
            any_success |= ok;
            any_failure |= !ok;
            next_report = now + pdMS_TO_TICKS(FOCUSCUBE_REPORT_INTERVAL_MS);
        }

        if (any_success) {
            dashboard_apply_live_data_from_task(&data);
        }

        if (any_failure) {
            dashboard_set_network_note_from_task("API RETRY", lv_color_hex(0xf59e0b),
                                                 "网络连接异常，正在重试（保留上次成功内容）");
        } else if (attempted && any_success) {
            ESP_LOGI(TAG, "backend refresh ok: status=%lu reminders=%lu report=%lu",
                     (unsigned long)(next_status / portTICK_PERIOD_MS),
                     (unsigned long)(next_reminders / portTICK_PERIOD_MS),
                     (unsigned long)(next_report / portTICK_PERIOD_MS));
        }

        vTaskDelay(pdMS_TO_TICKS(250));
    }
}

static void update_mock_data(lv_timer_t *timer)
{
    (void)timer;

    if (s_backend_live) {
        return;
    }

    static uint8_t step;
    static const int lux_values[] = {236, 184, 318, 265};
    static const char *light_labels[] = {"适宜", "偏暗", "偏亮", "适宜"};
    static const char *reports[] = {
        "当天大部分时间光照处于适宜范围，活动度整体较平稳。",
        "房间曾出现光线偏暗，建议下一次专注前打开台灯。",
        "今日专注时间有所提升，P4 正在显示每日复盘。",
        "S3 数据、后端 AI 复盘和 P4 屏幕展示闭环已就绪。"
    };
    static const char *reminders[] = {
        "1. [P3] 设备电量较低，请及时充电。\n2. [P2] 当前光线偏暗，建议打开台灯。",
        "1. [P3] 设备电量较低，请及时充电。\n2. [P2] 当前光线偏暗，建议打开台灯。",
        "1. [P2] 专注周期结束后请休息 5 分钟。",
        "1. [P1] 本地演示模式，等待联网。"
    };

    const uint8_t idx = step % 4;
    dashboard_set_badge("LOCAL DEMO", lv_color_hex(0x64748b));
    lv_label_set_text(s_s3_state_label, "S3：在线（模拟）");
    lv_label_set_text_fmt(s_lux_label, "当前光照：%d lux · %s", lux_values[idx], light_labels[idx]);
    lv_label_set_text_fmt(s_focus_label, "专注状态：专注中 · 剩余 %d 分钟", 16 - (step % 5));
    lv_label_set_text_fmt(s_battery_label, "电量：%d%% · 未充电", 18 - (step % 4));
    lv_label_set_text_fmt(s_report_label, "%s\n\n建议：每完成一个专注周期后休息 5 分钟。", reports[idx]);
    lv_label_set_text(s_reminder_label, reminders[idx]);
    lv_label_set_text_fmt(s_footer_label, "本地演示刷新 %u · 配置 Wi-Fi 后自动连接后端", step + 1);
    step++;
}

static void focuscube_dashboard_create(void)
{
    lv_obj_t *scr = lv_scr_act();
    lv_obj_set_style_bg_color(scr, lv_color_hex(0xf4f7fb), 0);
    lv_obj_set_style_bg_opa(scr, LV_OPA_COVER, 0);
    lv_obj_clear_flag(scr, LV_OBJ_FLAG_SCROLLABLE);

    create_text(scr, "FocusCube P4 Display", 36, 26, 620, &lv_font_montserrat_26, lv_color_hex(0x152238));
    create_text(scr, "S3 立方体 · 云端 AI 复盘 · 七寸屏展示", 38, 68, 760,
                &focuscube_font_cn_18, lv_color_hex(0x687386));

    s_badge_obj = lv_obj_create(scr);
    lv_obj_set_pos(s_badge_obj, 790, 30);
    lv_obj_set_size(s_badge_obj, 190, 46);
    lv_obj_set_style_radius(s_badge_obj, 23, 0);
    lv_obj_set_style_bg_color(s_badge_obj, lv_color_hex(0x64748b), 0);
    lv_obj_set_style_border_width(s_badge_obj, 0, 0);
    lv_obj_clear_flag(s_badge_obj, LV_OBJ_FLAG_SCROLLABLE);
    s_badge_label = create_text(s_badge_obj, "LOCAL DEMO", 0, 0, 190,
                                &lv_font_montserrat_18, lv_color_hex(0xffffff));
    lv_obj_set_style_text_align(s_badge_label, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_center(s_badge_label);

    lv_obj_t *status_card = create_card(scr, 36, 116, 448, 250, "S3 状态", lv_color_hex(0x2f80ed));
    s_s3_state_label = create_text(status_card, "S3：等待数据", 24, 62, 390,
                                   &focuscube_font_cn_18, lv_color_hex(0x152238));
    s_lux_label = create_text(status_card, "当前光照：等待数据", 24, 98, 390,
                              &focuscube_font_cn_18, lv_color_hex(0x2f80ed));
    s_focus_label = create_text(status_card, "专注状态：等待数据", 24, 134, 390,
                                &focuscube_font_cn_18, lv_color_hex(0x46505f));
    s_battery_label = create_text(status_card, "电量：等待数据", 24, 170, 390,
                                  &focuscube_font_cn_18, lv_color_hex(0x46505f));

    lv_obj_t *report_card = create_card(scr, 516, 116, 472, 408, "AI 每日复盘", lv_color_hex(0x8b5cf6));
    s_report_label = create_text(report_card, "等待 AI 复盘数据", 24, 66, 414,
                                 &focuscube_font_cn_18, lv_color_hex(0x1f2937));

    lv_obj_t *reminder_card = create_card(scr, 36, 390, 448, 134, "提醒", lv_color_hex(0xf59e0b));
    s_reminder_label = create_text(reminder_card, "等待提醒数据", 24, 58, 386,
                                   &focuscube_font_cn_18, lv_color_hex(0x1f2937));

    s_footer_label = create_text(scr, "", 38, 550, 900, &focuscube_font_cn_18, lv_color_hex(0x687386));

    if (focuscube_wifi_configured()) {
        dashboard_set_badge("CONNECTING", lv_color_hex(0x2f80ed));
        lv_label_set_text(s_footer_label, "正在连接 BUPT-portal 和后端...");
    } else {
        lv_timer_create(update_mock_data, 3000, NULL);
        update_mock_data(NULL);
    }
}

void app_main(void)
{
    bsp_display_cfg_t cfg = {
        .lvgl_port_cfg = ESP_LVGL_PORT_INIT_CONFIG(),
        .buffer_size = BSP_LCD_DRAW_BUFF_SIZE,
        .double_buffer = BSP_LCD_DRAW_BUFF_DOUBLE,
        .flags = {
            .buff_dma = true,
            .buff_spiram = false,
            .sw_rotate = true,
        }
    };
    lv_display_t *disp = bsp_display_start_with_config(&cfg);

    bsp_display_backlight_on();

    if (disp != NULL)
    {
        bsp_display_rotate(disp, LV_DISPLAY_ROTATION_180);
    }

    bsp_display_lock(0);

    focuscube_dashboard_create();

    bsp_display_unlock();

    xTaskCreate(focuscube_network_task, "focuscube_net", 12288, NULL, 5, NULL);
}
