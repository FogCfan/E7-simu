# Toolchain file CMake pour les Cortex-M55 de l'Alif Ensemble E7 (agent 02 : Toolchain & Build).
#
# Utilisation (c'est ce que fait `e7twin build` pour un projet CMake) :
#   cmake -G Ninja -S <src> -B <build> -DCMAKE_TOOLCHAIN_FILE=cmake/e7-m55.toolchain.cmake
#
# arm-none-eabi-gcc est cherche dans le PATH (e7twin.toolchain.env() place tools/ en tete), ou via
# -DE7_ARM_GCC_BIN=<dossier bin>. Le script d'edition de liens (placement memoire) est a la charge du
# projet : il doit venir du pack Alif / des regions de board/ensemble_e7.yaml, pas de ce fichier.

set(CMAKE_SYSTEM_NAME Generic)
set(CMAKE_SYSTEM_PROCESSOR cortex-m55)
set(CMAKE_TRY_COMPILE_TARGET_TYPE STATIC_LIBRARY)
# Le suffixe doit etre fixe par langage (CMAKE_EXECUTABLE_SUFFIX est ecrase par la plateforme Generic).
set(CMAKE_EXECUTABLE_SUFFIX_C   ".elf")
set(CMAKE_EXECUTABLE_SUFFIX_CXX ".elf")
set(CMAKE_EXECUTABLE_SUFFIX_ASM ".elf")
if(E7_CORE)
  message(STATUS "e7twin : coeur cible ${E7_CORE} (cortex-m55)")
endif()

if(E7_ARM_GCC_BIN)
  set(_e7_hint HINTS "${E7_ARM_GCC_BIN}" NO_DEFAULT_PATH)
endif()
find_program(E7_GCC arm-none-eabi-gcc ${_e7_hint})
if(NOT E7_GCC)
  message(FATAL_ERROR "arm-none-eabi-gcc introuvable : lancer scripts/install_tools.ps1 ou passer -DE7_ARM_GCC_BIN=")
endif()
find_program(E7_GXX     arm-none-eabi-g++     ${_e7_hint})
find_program(E7_OBJCOPY arm-none-eabi-objcopy ${_e7_hint})
find_program(E7_SIZE    arm-none-eabi-size    ${_e7_hint})
set(CMAKE_C_COMPILER   "${E7_GCC}")
set(CMAKE_CXX_COMPILER "${E7_GXX}")
set(CMAKE_ASM_COMPILER "${E7_GCC}")
set(CMAKE_OBJCOPY "${E7_OBJCOPY}")
set(CMAKE_SIZE    "${E7_SIZE}")

# Cortex-M55 : Armv8.1-M, MVE (Helium) entier + flottant et FPU double precision (implicites avec
# -mcpu=cortex-m55), ABI hard-float.
set(E7_CPU_FLAGS "-mcpu=cortex-m55 -mthumb -mfloat-abi=hard -mfpu=auto")
set(E7_COMMON_FLAGS "${E7_CPU_FLAGS} -ffunction-sections -fdata-sections -fstack-usage")

set(CMAKE_C_FLAGS_INIT   "${E7_COMMON_FLAGS} -std=c99")
set(CMAKE_CXX_FLAGS_INIT "${E7_COMMON_FLAGS}")
set(CMAKE_ASM_FLAGS_INIT "${E7_CPU_FLAGS}")

set(CMAKE_C_FLAGS_DEBUG_INIT          "-Og -g3")
set(CMAKE_C_FLAGS_RELEASE_INIT        "-O2 -g")
set(CMAKE_CXX_FLAGS_DEBUG_INIT        "-Og -g3")
set(CMAKE_CXX_FLAGS_RELEASE_INIT      "-O2 -g")

# Map + occupation des regions (--print-memory-usage) : lus par `e7twin build` / `e7twin check`.
set(CMAKE_EXE_LINKER_FLAGS_INIT
    "${E7_CPU_FLAGS} --specs=nosys.specs -Wl,--gc-sections -Wl,-Map=${CMAKE_BINARY_DIR}/firmware.map,--cref,--print-memory-usage -Wl,--no-warn-rwx-segments")

set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)
