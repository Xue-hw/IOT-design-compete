/*
 * SPDX-FileCopyrightText: 2025 Espressif Systems (Shanghai) CO LTD
 *
 * SPDX-License-Identifier: Apache-2.0
 */
#include "focuscube_data.h"

#include <stdio.h>
#include <string.h>

#include "cJSON.h"
#include "sdkconfig.h"

void focuscube_data_init(focuscube_data_t *data)
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

static bool json_measurement_is_valid(cJSON *obj)
{
    cJSON *valid = cJSON_GetObjectItemCaseSensitive(obj, "valid");
    return !cJSON_IsBool(valid) || cJSON_IsTrue(valid);
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

bool focuscube_parse_status(const char *json, focuscube_data_t *data)
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
            bool is_s3 = cJSON_IsString(device_id) && device_id->valuestring != NULL &&
                         strcmp(device_id->valuestring, CONFIG_FOCUSCUBE_DEVICE_ID) == 0;

            if (!is_s3) {
                continue;
            }

            cJSON *online = cJSON_GetObjectItemCaseSensitive(device, "online");
            data->has_s3_online = cJSON_IsBool(online);
            data->s3_online = cJSON_IsTrue(online);
            data->light_valid = false;
            data->imu_valid = false;
            data->focus_valid = false;
            data->power_valid = false;
            snprintf(data->device_id, sizeof(data->device_id), "%s", device_id->valuestring);
            copy_json_string(data->s3_summary, sizeof(data->s3_summary), device, "summary");

            cJSON *light = cJSON_GetObjectItemCaseSensitive(device, "light");
            if (cJSON_IsObject(light)) {
                data->light_valid = json_measurement_is_valid(light);
                cJSON *lux = cJSON_GetObjectItemCaseSensitive(light, "lux");
                if (data->light_valid && cJSON_IsNumber(lux)) {
                    data->lux = (int)(lux->valuedouble + 0.5);
                }

                cJSON *label = cJSON_GetObjectItemCaseSensitive(light, "label");
                if (data->light_valid && cJSON_IsString(label) && label->valuestring != NULL) {
                    const char *display_label = label->valuestring;
                    if (strcmp(display_label, "too_dim") == 0) {
                        display_label = "偏暗";
                    } else if (strcmp(display_label, "too_bright") == 0) {
                        display_label = "过亮";
                    } else if (strcmp(display_label, "suitable") == 0) {
                        display_label = "适宜";
                    }
                    snprintf(data->light_label, sizeof(data->light_label), "%s", display_label);
                }
            }

            cJSON *imu = cJSON_GetObjectItemCaseSensitive(device, "imu");
            data->imu_valid = cJSON_IsObject(imu) && json_measurement_is_valid(imu);

            cJSON *focus = cJSON_GetObjectItemCaseSensitive(device, "focus");
            if (cJSON_IsObject(focus)) {
                data->focus_valid = json_measurement_is_valid(focus);
                if (data->focus_valid) {
                    copy_json_string(data->focus_state, sizeof(data->focus_state), focus, "state");
                    get_json_int(focus, "remaining_s", &data->remaining_seconds);
                    get_json_int(focus, "session_count", &data->pomodoro_count);
                }
            }

            cJSON *power = cJSON_GetObjectItemCaseSensitive(device, "power");
            if (cJSON_IsObject(power)) {
                data->power_valid = json_measurement_is_valid(power);
                if (data->power_valid) {
                    get_json_int(power, "battery_pct", &data->battery_pct);
                    cJSON *charging = cJSON_GetObjectItemCaseSensitive(power, "charging");
                    data->charging = cJSON_IsTrue(charging);
                }
            }

            parsed = true;
            break;
        }
    }

    cJSON_Delete(root);
    return parsed;
}

bool focuscube_parse_report(const char *json, focuscube_data_t *data)
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

bool focuscube_parse_reminders(const char *json, focuscube_data_t *data)
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
