/*
 * SPDX-FileCopyrightText: 2025 Espressif Systems (Shanghai) CO LTD
 *
 * SPDX-License-Identifier: Apache-2.0
 */
#pragma once

#include <stdbool.h>

typedef struct {
    bool has_s3_online;
    bool s3_online;
    bool light_valid;
    bool imu_valid;
    bool focus_valid;
    bool power_valid;
    int lux;
    int focus_minutes;
    int pomodoro_count;
    int remaining_seconds;
    int battery_pct;
    bool charging;
    char device_id[40];
    char focus_state[24];
    char light_label[24];
    char s3_summary[128];
    char report[512];
    char suggestion[256];
    char reminders[512];
} focuscube_data_t;

void focuscube_data_init(focuscube_data_t *data);
bool focuscube_parse_status(const char *json, focuscube_data_t *data);
bool focuscube_parse_report(const char *json, focuscube_data_t *data);
bool focuscube_parse_reminders(const char *json, focuscube_data_t *data);
