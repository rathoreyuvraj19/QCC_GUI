/*
 * main.c
 *
 *  Created on: 05-Jun-2025
 *      Author: Yuvraj
 */
#include <user_common_include.h>
#include "mss_spi.h"


int main()
{

	FLASH_init();
	MSS_UART_init(	&g_mss_uart1,
					MSS_UART_115200_BAUD,
					//MSS_UART_57600_BAUD,
					//MSS_UART_9600_BAUD,
	                MSS_UART_DATA_8_BITS | MSS_UART_NO_PARITY | MSS_UART_ONE_STOP_BIT);

#ifdef SDTRM
	MSS_GPIO_init();
	MSS_GPIO_config(MSS_GPIO_30, MSS_GPIO_OUTPUT_MODE);
	MSS_GPIO_config(MSS_GPIO_31, MSS_GPIO_OUTPUT_MODE);

	// 0 is for MSS and 1 is fabric
	MSS_GPIO_set_output(MSS_GPIO_30, 0);
	MSS_GPIO_set_output(MSS_GPIO_31, 0);
#endif

#ifdef CQTRM
	MSS_GPIO_init();
	MSS_GPIO_config(MSS_GPIO_2, MSS_GPIO_OUTPUT_MODE);
	MSS_GPIO_config(MSS_GPIO_3, MSS_GPIO_OUTPUT_MODE);
	MSS_GPIO_config(MSS_GPIO_25, MSS_GPIO_OUTPUT_MODE);
	MSS_GPIO_config(MSS_GPIO_31, MSS_GPIO_OUTPUT_MODE);

	// 10 is for MSS and 11 is fabric
	MSS_GPIO_set_output(MSS_GPIO_25, 1);
	MSS_GPIO_set_output(MSS_GPIO_3, 0);

	MSS_GPIO_set_output(MSS_GPIO_2, 1);
	MSS_GPIO_set_output(MSS_GPIO_31, 0);
#endif

#ifdef XOTRM
	MSS_GPIO_init();
	MSS_GPIO_config(MSS_GPIO_2, MSS_GPIO_OUTPUT_MODE);
	MSS_GPIO_config(MSS_GPIO_3, MSS_GPIO_OUTPUT_MODE);
	MSS_GPIO_config(MSS_GPIO_25, MSS_GPIO_OUTPUT_MODE);
	MSS_GPIO_config(MSS_GPIO_31, MSS_GPIO_OUTPUT_MODE);

	// 01 is for MSS and 11 is fabric ---- MSS control to send Link
	MSS_GPIO_set_output(MSS_GPIO_25, MUX_SEL1);
	MSS_GPIO_set_output(MSS_GPIO_3, MUX_SEL2);

	MSS_GPIO_set_output(MSS_GPIO_2, MUX_SEL1);
	MSS_GPIO_set_output(MSS_GPIO_31, MUX_SEL2);;

#endif

	uint8_t manufacturer_id, device_id;
	FLASH_read_device_id(&manufacturer_id, &device_id);
	uint8_t get_status = FLASH_get_status();

//
//	uint32_t spi_add = 0x1000;
//	uint8_t rx_buff1[256] = {0};
//	FLASH_read(spi_add, rx_buff1, 256);
//
//	uint32_t spi_add2 = IAP_IMAGE_ADDRESS;
//	uint8_t rx_buff2[256] = {0};
//	FLASH_read(spi_add2, rx_buff2, 256);
//
//	uint32_t spi_add3 = 0x00000000;
//	uint8_t rx_buff3[256] = {0};
//	FLASH_read(spi_add3, rx_buff3, 256);
//
//	uint8_t rx_buff2[256]= {0};
//	FLASH_read(spi_add + 256, rx_buff2, 256);
//
//	uint8_t rx_buff3[256]= {0};
//	FLASH_read(spi_add + 2 * 256, rx_buff3, 256);
//
//	uint8_t rx_buff4[256]= {0};
//	FLASH_read(spi_add + 4096 * 74, rx_buff4, 256);
//
//	uint8_t rx_buff5[256]= {0};
//	FLASH_read(spi_add + 4096 * 75, rx_buff5, 256);
//
//	uint8_t rx_buff6[256]= {0};
//	FLASH_read(spi_add + 4096 * 75 + 256, rx_buff6, 256);
//	uint8_t rx_buff7[256]= {0};
//	FLASH_read(spi_add + 4096 * 75 + 2*256, rx_buff7, 256);
//	uint8_t rx_buff8[256]= {0};
//	FLASH_read(spi_add + 4096 * 75 + 11*256, rx_buff8, 256);
//	uint8_t rx_buff9[256]= {0};
//	FLASH_read(spi_add + 4096 * 75 + 12*256, rx_buff9, 256);


//	uint8_t result = MSS_SYS_initiate_iap(
//			MSS_SYS_PROG_AUTHENTICATE, 0x04000000);
//	uint8_t result2 = MSS_SYS_initiate_iap(
//			MSS_SYS_PROG_VERIFY, 0x04000000);

//	while(1){
//		sendLinkRes();
//	}

	LRU_info_type_def LRU_info= {0};
	get_LRU_info(&LRU_info);
	sendLinkRes();

	for(volatile int i=0 ; i<15000 ;i++);	// wait for some time to get Link response and then change the MUX control to fabric

// ---- Fabric Control
	MSS_GPIO_set_output(MSS_GPIO_25, 1);
	MSS_GPIO_set_output(MSS_GPIO_3, 1);

	MSS_GPIO_set_output(MSS_GPIO_2, 1);
	MSS_GPIO_set_output(MSS_GPIO_31, 1);

	while (1) {
			volatile uint8_t temp = (*COREGPIO3_INPUT_REG && 1);	// Change to MSS from fabric based on the mode change flag
			if (temp == 1) {
				MSS_GPIO_set_output(MSS_GPIO_25, 1);
				MSS_GPIO_set_output(MSS_GPIO_3, 0);

				MSS_GPIO_set_output(MSS_GPIO_2,1);
				MSS_GPIO_set_output(MSS_GPIO_31, 0);;
				break;
			}
		}

	while(1){
		wait_for_new_request(&LRU_info);
	}


	return 0;
}



