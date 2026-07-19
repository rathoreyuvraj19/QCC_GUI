/*
 * user_functions.h
 *
 *  Created on: 11-Jun-2025
 *      Author: Yuvraj
 */

#ifndef USER_FUNCTIONS_INC_USER_FUNCTIONS_H_
#define USER_FUNCTIONS_INC_USER_FUNCTIONS_H_
#include <user_common_include.h>

#define BIT_STREAM_PACKET_HEADER_SIZE 10
#define CMD_MSG_PACKET_SIZE 		10

/* QCC's low-speed FIFO drain is 32-bit-word based and always transmits
 * all 4 bytes of every word, so every stream QCC sends us arrives
 * zero-padded up to a multiple of 4 bytes. Always RECEIVE the padded
 * length (and use only the real first N bytes) so the pad bytes are
 * consumed here and never shift the framing of the next packet. */
#define QCC_FIFO_PAD4(n) ((((n) + 3u) / 4u) * 4u)

#define COREGPIO_INPUT_OFFSET_REG 0x90
#define COREGPIO0_INPUT_REG  ((uint32_t*)(0x50000000U + COREGPIO_INPUT_OFFSET_REG))
#define COREGPIO1_INPUT_REG  ((uint16_t*)(0x50001000U + COREGPIO_INPUT_OFFSET_REG))
#define COREGPIO3_INPUT_REG	 ((uint8_t*) (0x50002000U + COREGPIO_INPUT_OFFSET_REG))

#define IMG_IS_GOLDEN 	  1
#define IMG_IS_NOT_GOLDEN 0

#define IAP_MODE_AUTHENTICATION 1
#define IAP_MODE_PROGRAM 		2
#define IAP_MODE_VERIFY 		3

#define SPI_DIR_ADDRESS		 	0x0000000u
#define GOLDEN_IMAGE_ADDRESS 	0x1000u
#define IAP_IMAGE_ADDRESS 		0x00400000u

#define MUX_SEL1 1
#define MUX_SEL2 0

typedef struct LRU_info_type_def{
	uint8_t  fabric_version_no;
	uint8_t  mfg_id;
	uint8_t  lm_id;
	uint8_t  part_no;
	uint16_t serial_no;
}LRU_info_type_def;

typedef struct common_byte_type_def{
	uint8_t header;
	uint8_t packet_size_identifier;
	uint8_t command_type;
	uint8_t status_type_and_sub_status_type;
	uint8_t msg_counter;
}common_byte_type_def;

typedef struct LRU_info_response_type_def{
	common_byte_type_def common_bytes;
	uint8_t              mfg_id_and_part_number;
	uint8_t              serial_num_lsb;
	uint8_t              serial_num_msb;
	uint8_t              fw_version;
	uint8_t              check_sum;
}LRU_info_response_type_def;

typedef struct Error_MSG_response_format_type_def{
	common_byte_type_def common_bytes;
	uint8_t header_error;
	uint8_t CRC_error;
	uint8_t timeout_error;
	uint8_t reserved;
	uint8_t check_sum;
}Error_MSG_response_format_type_def;

void wait_for_new_request(LRU_info_type_def* pLRU_info);

void get_LRU_info(LRU_info_type_def* LRU_info);

void sendLinkRes();

void send_LRU_info(LRU_info_type_def* LRU_info);

void getCheckSum(uint8_t* pMsg);

void mode_change_mss_to_fab(void);

void recieve_bit_stream(uint16_t total_packet_count,uint16_t packetSize, uint8_t img_is_golden);

uint8_t headerAndCheckSumCheck(uint8_t* rx_buff,uint32_t size);

void update_spi_dir();
uint8_t write_to_spi(uint8_t* write_buff,uint32_t packet_size, uint32_t packet_index,uint8_t image_is_golden);
void send_receive_packet_ack(uint16_t packet_index,uint8_t pass_or_fail);//Pass = 1 fail = 0;

void iap_authenticate(uint8_t image_is_golden);
void iap_program(uint8_t image_is_golden);
void iap_verify(uint8_t image_is_golden);

void send_error(uint8_t header_err, uint8_t timeout, uint8_t crc_error);
#endif /* USER_FUNCTIONS_INC_USER_FUNCTIONS_H_ */
