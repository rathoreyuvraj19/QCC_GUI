/*
 * user_specific_header.h
 *
 *  Created on: 13-Jun-2025
 *      Author: Yuvraj
 */

#ifndef USER_COMMON_INCLUDE_H_
#define USER_COMMON_INCLUDE_H_

#define CQTRM
//#define SDTRM
//#define XOTRM

#include "mss_gpio.h"
#include "mss_uart.h"
#include "core_gpio.h"
#include "user_functions.h"
#include "CMSIS/system_m2sxxx.h"
#include <stdio.h>
#include <string.h>
#include "micron.h"
#include "mss_sys_services.h"

#define LINK_REQ 							0x30
#define CMD_TYPE_GET_LRU_INFO 				0x31
#define CMD_TYPE_MODE_CHANGE_MSS_TO_FAB 	0x32
#define CMD_TYPE_START_BIT_STREAM_REC       0x33
#define TYPE_BIT_STREAM_PACKET 			    0x34
#define TYPE_ACK_MSG						0x35
#define CMD_TYPE_FW_UPDATE_COMMAND 			0x36






#endif /* USER_COMMON_INCLUDE_H_ */
