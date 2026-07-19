#include <user_common_include.h>
#include "mss_spi.h"

void wait_for_new_request(LRU_info_type_def* pLRU_info) {
	uint32_t byte_count = 0;
	/* Receive the QCC-FIFO-padded length (12 for a 10-byte command) so the
	 * 2 zero pad bytes are consumed here instead of desyncing the next
	 * packet; only the first CMD_MSG_PACKET_SIZE bytes are ever parsed. */
	uint8_t recv_data[QCC_FIFO_PAD4(CMD_MSG_PACKET_SIZE)] = { 0 };
	uint8_t temp = 0;
	uint32_t timer = 0;
	uint32_t packetSize = QCC_FIFO_PAD4(CMD_MSG_PACKET_SIZE);
 	while (byte_count < packetSize) {
		if (MSS_UART_get_rx(&g_mss_uart1, &temp, 1) > 0) {
			timer = 0;
			recv_data[byte_count++] = temp;
			if (byte_count == packetSize) {
				byte_count = 0;
				break;
			}
		} else {
			if (timer > 1000000) { // Some random timeout iam giving
//				if (byte_count > 0) {
//					send_error(0, 1, 0);
//				}
				timer = 0;
				byte_count = 0;
				return;
			} else {
				timer++;
			}
		}
	}
	// TODO: check header and checksum and generate error msg accordingly and also add timeout error

	if (!headerAndCheckSumCheck(recv_data, CMD_MSG_PACKET_SIZE)) {
		//Send an error msg based on the type of error
		return;
	}
//	uint16_t fw_packet_size_lsb = (uint16_t) recv_data[5];
//	uint16_t fw_packet_size_msb = (uint16_t) recv_data[6];
//	uint16_t fw_packet_count_lsb = (uint16_t) recv_data[7];
//	uint16_t fw_packet_count_msb =  (uint16_t)recv_data[8];
//	uint16_t total_packet_count = (fw_packet_count_msb<<8) | fw_packet_count_lsb;
//	uint16_t bit_stream_packetSize = (fw_packet_size_msb<<8) | fw_packet_size_lsb;
	volatile uint8_t cmd_type = recv_data[2];
	switch (cmd_type) {
	case LINK_REQ:
		sendLinkRes();
		break;
	case CMD_TYPE_START_BIT_STREAM_REC: {
		uint8_t image_is_golden = recv_data[3];
		uint16_t fw_packet_size_lsb = (uint16_t) recv_data[5];
		uint16_t fw_packet_size_msb = (uint16_t) recv_data[6];
		uint16_t fw_packet_count_lsb = (uint16_t) recv_data[7];
		uint16_t fw_packet_count_msb = (uint16_t) recv_data[8];
		uint16_t total_packet_count = (fw_packet_count_msb << 8)
				| fw_packet_count_lsb;
		uint16_t bit_stream_packetSize = (fw_packet_size_msb << 8)
				| fw_packet_size_lsb;
		recieve_bit_stream(total_packet_count, bit_stream_packetSize, image_is_golden);
		if(image_is_golden){
					update_spi_dir();
				}
		break;
	}
	case CMD_TYPE_FW_UPDATE_COMMAND: {
		volatile uint8_t image_is_golden = recv_data[3];
		volatile uint8_t iap_mode = (0x0F) & recv_data[5];
		if (iap_mode == IAP_MODE_AUTHENTICATION) {
			// Authenticate
			iap_authenticate(image_is_golden);
		} else if (iap_mode == IAP_MODE_PROGRAM) {
			// Program
			iap_program(image_is_golden);
		} else if (iap_mode == IAP_MODE_VERIFY) {
			// Verify
			iap_verify(image_is_golden);
		} else {
			//Invalid
		}
		break;
	}
	case CMD_TYPE_GET_LRU_INFO: {
		get_LRU_info(pLRU_info);
		send_LRU_info(pLRU_info);
		break;
	}
	case CMD_TYPE_MODE_CHANGE_MSS_TO_FAB: {
		mode_change_mss_to_fab();
		return;
		break;
	}

	}
}

void recieve_bit_stream(uint16_t total_packet_count, uint16_t packetSize, uint8_t img_is_golden) {
	uint32_t byte_count = 0;
	/* Receive the QCC-FIFO-padded chunk length (e.g. 4108 for a 10+4096
	 * chunk) so the zero pad bytes are consumed here; only the first
	 * packetSize + BIT_STREAM_PACKET_HEADER_SIZE bytes are ever used. */
	uint32_t padded_size = QCC_FIFO_PAD4(packetSize + BIT_STREAM_PACKET_HEADER_SIZE);
	uint8_t recv_data[QCC_FIFO_PAD4(packetSize + BIT_STREAM_PACKET_HEADER_SIZE)];
	memset(recv_data, 0, padded_size);
	uint8_t bitstream_packet[packetSize];
	memset(bitstream_packet, 0, packetSize);
	uint8_t* p_recv_buff = recv_data;
	uint32_t timer = 0;
	uint16_t packet_index = 0;
	uint8_t pck_trnsf_pass_or_fail = 1; // 1 = pass and 0 = fail
	while (packet_index < total_packet_count) {//TODO: Check packet_index <= total_packet_count OR <?????????????????????
		memset(recv_data, 0, sizeof(recv_data));
		p_recv_buff = recv_data;
		while (byte_count < padded_size) {
			/* Never request past the end of this chunk's buffer -- caps the
			 * read so a burst can neither overrun recv_data nor overshoot
			 * the exact byte_count == padded_size terminating check. */
			uint32_t want = padded_size - byte_count;
			if (want > 16) {
				want = 16;
			}
			uint32_t rx_size = MSS_UART_get_rx(&g_mss_uart1, p_recv_buff, want);
			if (rx_size > 0) {
				timer = 0;
				p_recv_buff += rx_size;
				byte_count += rx_size;
				if (byte_count == padded_size) {
					byte_count = 0;
					packet_index++;
					break;
				}
			} else {
				if (timer > 2000000) { // Some random timeout
					timer = 0;
					send_error(0, 1, 0);
					packet_index = 0;
					byte_count = 0;
					return;
				} else {
					timer++;
				}
			}
		}
		memcpy(bitstream_packet, &recv_data[BIT_STREAM_PACKET_HEADER_SIZE],packetSize);
		/////if()
		pck_trnsf_pass_or_fail = write_to_spi(bitstream_packet, packetSize, packet_index - 1, img_is_golden);
		send_receive_packet_ack(packet_index-1, pck_trnsf_pass_or_fail);
	}

}
void update_spi_dir(){
	uint32_t spi_dir_addr = SPI_DIR_ADDRESS;
	uint32_t golden_img_addr = GOLDEN_IMAGE_ADDRESS;
	uint16_t golden_img_ver = 0x00;
	uint8_t write_buff_temp[6] = { 0 };
	write_buff_temp[0] = (golden_img_addr & 0xFF);
	write_buff_temp[1] = ((golden_img_addr >> 8) & 0xFF);
	write_buff_temp[2] = ((golden_img_addr >> 16) & 0xFF);
	write_buff_temp[3] = ((golden_img_addr >> 24) & 0xFF);
	write_buff_temp[4] = ((golden_img_ver) & 0xFF);
	write_buff_temp[5] = ((golden_img_ver >> 8) & 0xFF);

	uint8_t manufacturer_id, device_id;
	FLASH_read_device_id(&manufacturer_id, &device_id);

	FLASH_erase_4k_block(spi_dir_addr);
	FLASH_program(spi_dir_addr, write_buff_temp, sizeof(write_buff_temp));
	uint8_t rx_buff_spi_dir[6] = { 0 };
	FLASH_read(spi_dir_addr, rx_buff_spi_dir, 6);
	FLASH_read(spi_dir_addr, rx_buff_spi_dir, 6);
}

uint8_t write_to_spi(uint8_t* write_buff, uint32_t packet_size, uint32_t packet_index, uint8_t image_is_golden) {
	FLASH_init();
	if (image_is_golden == 1) { //[0x1000 – 0x3FFFFF] golden image location
		uint32_t golden_img_addr = GOLDEN_IMAGE_ADDRESS;
		uint16_t golden_img_ver = 0x00;
		uint32_t curr_block_addr = golden_img_addr + 4 * 1024 * packet_index;
		FLASH_erase_4k_block(curr_block_addr);
		uint32_t no_of_writes = packet_size / 256;
		for (uint32_t i = 0; i < no_of_writes; i++) {
			uint32_t curr_sector_addr = curr_block_addr + 256 * i;
			if(curr_sector_addr >= IAP_IMAGE_ADDRESS){
				printf("hello");
			}
			uint8_t temp[256];
			memcpy(temp, write_buff + 256 * i, 256);
			FLASH_program(curr_sector_addr, temp, 256);
		}
	} else {		//[0x400000 -0x7FFFFF] // spi image location
		uint32_t image_address = IAP_IMAGE_ADDRESS;
		uint32_t curr_block_addr = image_address + 4 * 1024 * packet_index;
		FLASH_erase_4k_block(curr_block_addr);

		uint32_t no_of_writes = packet_size / 256;
		for (volatile uint32_t i = 0; i < no_of_writes; i++) {
			uint32_t curr_sector_addr = curr_block_addr + 256 * i;
			uint8_t temp[256];
			memcpy(temp, write_buff + 256 * i, 256);
			FLASH_program(curr_sector_addr, temp, 256);
		}
	}
	return 1;
}
;

void send_receive_packet_ack(uint16_t packet_index, uint8_t pass_or_fail) {
	uint8_t packet_ack[10] = { 0 };
	packet_ack[0] = 0xAA;
	packet_ack[1] = 0x00;
	packet_ack[2] = TYPE_ACK_MSG;
	packet_ack[3] = 0x00;
	packet_ack[4] = 0x00;
	packet_ack[5] = (uint8_t) packet_index;
	packet_ack[6] = (uint8_t) (packet_index >> 8);
	packet_ack[7] = pass_or_fail;
	packet_ack[8] = 0x00;
	getCheckSum(packet_ack);
	MSS_UART_polled_tx(&g_mss_uart1, packet_ack, 10);
}

void get_LRU_info(LRU_info_type_def* pLRU_info) {
	volatile uint16_t i_version_and_mfgid_info_from_fabric = *COREGPIO1_INPUT_REG; // 0x00 & version_no[7 downto 0]
	volatile uint32_t i_mfg_part_info_from_fabric = *COREGPIO0_INPUT_REG; // lm_id[7:0] & part_number[7 downto 0] & serial_no_msb[7 downto 0] & serial_no_lsb[7 downto 0]
	pLRU_info->mfg_id = ((i_version_and_mfgid_info_from_fabric >> 8) & 0x00FF);
	pLRU_info->fabric_version_no = (i_version_and_mfgid_info_from_fabric & 0x00FF);
	pLRU_info->lm_id = (i_mfg_part_info_from_fabric >> 24) & 0xFF;
	pLRU_info->part_no = (i_mfg_part_info_from_fabric >> 16) & 0xFF;
	pLRU_info->serial_no = (i_mfg_part_info_from_fabric) & 0xFFFF;
}

void sendLinkRes() {
	uint8_t Link_Res_Packet[10] = { 0xAA, 0x00, 0x34, 0x00, 0x00, 0xB1, 0xB2,
			0xB3, 0xB4 };
	getCheckSum((uint8_t*) Link_Res_Packet);
	MSS_UART_polled_tx(&g_mss_uart1, Link_Res_Packet, 10);
}

void send_LRU_info(LRU_info_type_def* pLRU_info) {
	LRU_info_response_type_def send_data = { 0 };
	send_data.common_bytes.header = 0xAA;
	send_data.common_bytes.packet_size_identifier = 0x00;
	send_data.common_bytes.command_type = 0x30;
	send_data.common_bytes.status_type_and_sub_status_type = 0x00;
	send_data.common_bytes.msg_counter = 0x00; // use extern global variable here

	send_data.mfg_id_and_part_number = (pLRU_info->mfg_id <<4)|(pLRU_info->part_no & 0x0F);

	send_data.serial_num_lsb = (pLRU_info->serial_no & 0xFF);
	send_data.serial_num_msb = ((pLRU_info->serial_no >> 8) & (0xFF));
	send_data.fw_version = (pLRU_info->fabric_version_no);
	getCheckSum((uint8_t*) &send_data);
	MSS_UART_polled_tx(&g_mss_uart1, (uint8_t*) &send_data, sizeof(LRU_info_response_type_def));

}

void getCheckSum(uint8_t* pMsg) {
	uint8_t packetSize = pMsg[1] * 5 + 10;
	uint8_t checkSum = 0;
	for (uint8_t i = 0; i < packetSize; i++) {
		checkSum ^= pMsg[i];
	}
	pMsg[packetSize - 1] = checkSum;
}

#ifdef SDTRM
void mode_change_mss_to_fab() {
	MSS_GPIO_set_output(MSS_GPIO_30, 1);
	MSS_GPIO_set_output(MSS_GPIO_31, 1);
	while (1) {
		volatile uint8_t temp = (*COREGPIO3_INPUT_REG && 1);	// Change to MSS from fabric based on the mode change flag
		if (temp == 1) {
			MSS_GPIO_set_output(MSS_GPIO_30, 0);
			MSS_GPIO_set_output(MSS_GPIO_31, 0);
			break;
		}
	}
}
#endif

#ifdef CQTRM
void mode_change_mss_to_fab() {
	MSS_GPIO_set_output(MSS_GPIO_25, 1);
	MSS_GPIO_set_output(MSS_GPIO_3, 1);

	MSS_GPIO_set_output(MSS_GPIO_2, 1);
	MSS_GPIO_set_output(MSS_GPIO_31, 1);
	while (1) {
		volatile uint8_t temp = (*COREGPIO3_INPUT_REG && 1);	// Change to MSS from fabric based on the mode change flag
		if (temp == 1) {
			MSS_GPIO_set_output(MSS_GPIO_25, 1);
			MSS_GPIO_set_output(MSS_GPIO_3, 0);

			MSS_GPIO_set_output(MSS_GPIO_2, 1);
			MSS_GPIO_set_output(MSS_GPIO_31, 0);
			break;
		}
	}
}
#endif

#ifdef XOTRM
void mode_change_mss_to_fab() {
	MSS_GPIO_set_output(MSS_GPIO_25, 1);
	MSS_GPIO_set_output(MSS_GPIO_3, 1);

	MSS_GPIO_set_output(MSS_GPIO_2, 1);
	MSS_GPIO_set_output(MSS_GPIO_31, 1);
	while (1) {
		volatile uint8_t temp = (*COREGPIO3_INPUT_REG && 1);	// Change to MSS from fabric based on the mode change flag
		if (temp == 1) {
			MSS_GPIO_set_output(MSS_GPIO_25, MUX_SEL1);
			MSS_GPIO_set_output(MSS_GPIO_3, MUX_SEL2);

			MSS_GPIO_set_output(MSS_GPIO_2, MUX_SEL1);
			MSS_GPIO_set_output(MSS_GPIO_31, MUX_SEL2);
			break;
		}
	}
}
#endif

///// Check the header and checksum value for error message display
uint8_t headerAndCheckSumCheck(uint8_t* rx_buff, uint32_t size) {
	return 1;
}

void iap_authenticate(uint8_t image_is_golden) {
	uint8_t result;
	if(image_is_golden){// Image is golden
		MSS_SPI_set_slave_select(&g_mss_spi0, MSS_SPI_SLAVE_0); // Slave SELECT signal asserted
		g_mss_spi0.hw_reg->CONTROL |= (GOLDEN_IMAGE_ADDRESS);
		MSS_SYS_init(MSS_SYS_NO_EVENT_HANDLER);
		result = MSS_SYS_initiate_iap(MSS_SYS_PROG_AUTHENTICATE, GOLDEN_IMAGE_ADDRESS);
	}else{// Image is not golden
		MSS_SPI_set_slave_select(&g_mss_spi0, MSS_SPI_SLAVE_0); // Slave SELECT signal asserted
		g_mss_spi0.hw_reg->CONTROL |= (IAP_IMAGE_ADDRESS);
		MSS_SYS_init(MSS_SYS_NO_EVENT_HANDLER);
		result = MSS_SYS_initiate_iap(MSS_SYS_PROG_AUTHENTICATE, IAP_IMAGE_ADDRESS);
	}

	uint8_t Res_Packet[10] = { 0xAA, 0x00, 0x35, 0x00, 0x00, IAP_MODE_AUTHENTICATION , result, 0x00, 0x00 };
	getCheckSum((uint8_t*) Res_Packet);
	MSS_UART_polled_tx(&g_mss_uart1, Res_Packet, 10);
}

void iap_program(uint8_t image_is_golden) {
	uint8_t result;
	if(image_is_golden){// Image is golden
		MSS_SPI_set_slave_select(&g_mss_spi0, MSS_SPI_SLAVE_0); // Slave SELECT signal asserted
		g_mss_spi0.hw_reg->CONTROL |= (GOLDEN_IMAGE_ADDRESS);
		MSS_SYS_init(MSS_SYS_NO_EVENT_HANDLER);
		result = MSS_SYS_initiate_iap(MSS_SYS_PROG_PROGRAM, GOLDEN_IMAGE_ADDRESS);
	}else{// Image is not golden
		MSS_SPI_set_slave_select(&g_mss_spi0, MSS_SPI_SLAVE_0); // Slave SELECT signal asserted
		g_mss_spi0.hw_reg->CONTROL |= (IAP_IMAGE_ADDRESS);
		MSS_SYS_init(MSS_SYS_NO_EVENT_HANDLER);
		result = MSS_SYS_initiate_iap(MSS_SYS_PROG_PROGRAM, IAP_IMAGE_ADDRESS);
	}

	do {
		;
	} while (result != 0x06);
}

void iap_verify(uint8_t image_is_golden) {
	uint8_t result;
	if(image_is_golden){// Image is golden
		MSS_SPI_set_slave_select(&g_mss_spi0, MSS_SPI_SLAVE_0); // Slave SELECT signal asserted
		g_mss_spi0.hw_reg->CONTROL |= (GOLDEN_IMAGE_ADDRESS);
		MSS_SYS_init(MSS_SYS_NO_EVENT_HANDLER);
		result = MSS_SYS_initiate_iap(MSS_SYS_PROG_VERIFY, GOLDEN_IMAGE_ADDRESS);
	}else{// Image is not golden
		MSS_SPI_set_slave_select(&g_mss_spi0, MSS_SPI_SLAVE_0); // Slave SELECT signal asserted
		g_mss_spi0.hw_reg->CONTROL |= (IAP_IMAGE_ADDRESS);
		MSS_SYS_init(MSS_SYS_NO_EVENT_HANDLER);
		result = MSS_SYS_initiate_iap(MSS_SYS_PROG_VERIFY, IAP_IMAGE_ADDRESS);
	}
	uint8_t Res_Packet[10] = { 0xAA, 0x00, 0x35, 0x00, 0x00, IAP_MODE_VERIFY, result, 0x00, 0x00 };
	getCheckSum((uint8_t*) Res_Packet);
	MSS_UART_polled_tx(&g_mss_uart1, Res_Packet, 10);
}

void send_error(uint8_t header_err, uint8_t timeout, uint8_t crc_error){
	Error_MSG_response_format_type_def data_packet;
	memset(&data_packet,0,sizeof(Error_MSG_response_format_type_def));
	data_packet.common_bytes.header = 0xAA;
	data_packet.common_bytes.packet_size_identifier = 0x00;
	data_packet.common_bytes.command_type = 0x3F;
	data_packet.common_bytes.status_type_and_sub_status_type = 0x00;
	data_packet.common_bytes.msg_counter = 0x00;

	data_packet.header_error = header_err;
	data_packet.timeout_error = timeout;
	data_packet.CRC_error = crc_error;
	data_packet.reserved = 0x00;
	getCheckSum((uint8_t*)&data_packet);
	MSS_UART_polled_tx(&g_mss_uart1, (uint8_t*)&data_packet , 10);
}
