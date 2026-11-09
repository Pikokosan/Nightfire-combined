add_library(usermod_nrf_radio INTERFACE)

target_sources(usermod_nrf_radio INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/nrf_radio.c
)

target_include_directories(usermod_nrf_radio INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}
)

target_link_libraries(usermod INTERFACE usermod_nrf_radio)
