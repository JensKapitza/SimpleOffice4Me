cmake_minimum_required(VERSION 3.22.1)
project(SimpleOfficeAndroidOcr LANGUAGES C CXX)

include(FetchContent)
include(CMakePackageConfigHelpers)

set(CMAKE_POSITION_INDEPENDENT_CODE ON)
set(CMAKE_C_VISIBILITY_PRESET hidden)
set(CMAKE_CXX_VISIBILITY_PRESET hidden)
set(CMAKE_VISIBILITY_INLINES_HIDDEN ON)
set(BUILD_SHARED_LIBS OFF)
# Only the Android/Pillow-decoded 8-bit raster enters this library. No image
# codecs, archive/network inputs, legacy engine, training tools or CLI ship.
foreach(option ENABLE_PNG ENABLE_JPEG ENABLE_GIF ENABLE_TIFF ENABLE_WEBP
               ENABLE_OPENJPEG BUILD_PROG BUILD_TESTING BUILD_EXECUTABLE
               BUILD_TRAINING_TOOLS BUILD_TESTS ENABLE_PRECOMPILED_HEADERS
               ENABLE_CCACHE OPENMP_BUILD INSTALL_CONFIGS)
    set(${option} OFF CACHE BOOL "Not used by the APK OCR library" FORCE)
endforeach()
foreach(option GRAPHICS_DISABLED DISABLED_LEGACY_ENGINE DISABLE_TIFF
               DISABLE_ARCHIVE DISABLE_CURL)
    set(${option} ON CACHE BOOL "Not used by the APK OCR library" FORCE)
endforeach()
# Tesseract text processing doesn't need any optional Leptonica compression.
set(ENABLE_ZLIB OFF CACHE BOOL "Leptonica codecs are not shipped" FORCE)

function(pinned_source name owner repository version commit)
    FetchContent_Declare(${name}
        GIT_REPOSITORY "https://github.com/${owner}/${repository}.git"
        GIT_TAG "${version}" GIT_SHALLOW TRUE GIT_SUBMODULES ""
        UPDATE_DISCONNECTED TRUE)
    FetchContent_GetProperties(${name})
    if(NOT ${name}_POPULATED)
        FetchContent_Populate(${name})
    endif()
    find_package(Git REQUIRED)
    execute_process(COMMAND "${GIT_EXECUTABLE}" -C "${${name}_SOURCE_DIR}" rev-parse HEAD
        OUTPUT_VARIABLE actual OUTPUT_STRIP_TRAILING_WHITESPACE
        COMMAND_ERROR_IS_FATAL ANY)
    if(NOT actual STREQUAL commit)
        message(FATAL_ERROR "${repository} source revision differs from the approved pin")
    endif()
    set(${name}_SOURCE_DIR "${${name}_SOURCE_DIR}" PARENT_SCOPE)
    set(${name}_BINARY_DIR "${${name}_BINARY_DIR}" PARENT_SCOPE)
endfunction()

pinned_source(ocr_lept DanBloomberg leptonica 1.87.0 13275a278eb55b5746e33f95fbf5a2c8f604b3ab)
pinned_source(ocr_core tesseract-ocr tesseract 5.5.5 4292f6890aa86ef64eba32090358f40f69dd6d28)
pinned_source(ocr_cpu google cpu_features v0.10.1 d3b2440fcfc25fe8e6d0d4a85f06d68e98312f5b)

add_subdirectory("${ocr_lept_SOURCE_DIR}" "${ocr_lept_BINARY_DIR}" EXCLUDE_FROM_ALL)
add_subdirectory("${ocr_cpu_SOURCE_DIR}" "${ocr_cpu_BINARY_DIR}" EXCLUDE_FROM_ALL)
add_library(CpuFeatures::ndk_compat ALIAS ndk_compat)

# Use the targets in this build, never Android runner/host libraries.
set(Leptonica_DIR "${CMAKE_CURRENT_BINARY_DIR}/ocr-config")
set(CpuFeaturesNdkCompat_DIR "${Leptonica_DIR}")
file(MAKE_DIRECTORY "${Leptonica_DIR}")
file(WRITE "${Leptonica_DIR}/LeptonicaConfig.cmake"
    "set(Leptonica_LIBRARIES leptonica)\nset(Leptonica_INCLUDE_DIRS \"${ocr_lept_SOURCE_DIR}/src;${ocr_lept_BINARY_DIR}/src\")\n")
write_basic_package_version_file("${Leptonica_DIR}/LeptonicaConfigVersion.cmake"
    VERSION 1.87.0 COMPATIBILITY SameMajorVersion)
file(WRITE "${CpuFeaturesNdkCompat_DIR}/CpuFeaturesNdkCompatConfig.cmake"
    "set(CpuFeaturesNdkCompat_FOUND TRUE)\n")
# A fixed CMake 3.22.1 avoids trying to execute Tesseract's optional TIFF
# detection program on the build host while cross-compiling.
add_subdirectory("${ocr_core_SOURCE_DIR}" "${ocr_core_BINARY_DIR}" EXCLUDE_FROM_ALL)

add_library(simpleoffice_ocr SHARED android_photo_ocr.cpp)
target_compile_features(simpleoffice_ocr PRIVATE cxx_std_17)
target_include_directories(simpleoffice_ocr PRIVATE "${ocr_core_BINARY_DIR}/include")
target_link_libraries(simpleoffice_ocr PRIVATE libtesseract)
target_link_options(simpleoffice_ocr PRIVATE "-Wl,--exclude-libs,ALL")
