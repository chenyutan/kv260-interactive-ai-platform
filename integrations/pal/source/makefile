NAME = sdlpal
INC_DIR += ./include
BUILD_DIR ?= ./build

OBJ_DIR ?= $(BUILD_DIR)/
BINARY ?= $(BUILD_DIR)/$(NAME)

.DEFAULT_GOAL = app

# Compilation flags
CC  = gcc
CXX = g++
LD  = g++
SDL_CONFIG ?= sdl-config
INCLUDES  = $(addprefix -I, $(INC_DIR))
override CPPFLAGS += $(INCLUDES) $(shell $(SDL_CONFIG) --cflags)
CFLAGS   ?= -O2 -Wall
CXXFLAGS ?= -O2 -Wall
override CFLAGS   += -MMD
override CXXFLAGS += -MMD

# Files to be compiled
C_SRCS   = $(shell find src/ -name "*.c")
CXX_SRCS = $(shell find src/ -name "*.cpp")
OBJS = $(C_SRCS:src/%.c=$(OBJ_DIR)/%.o) $(CXX_SRCS:src/%.cpp=$(OBJ_DIR)/%.o) 

# Compilation patterns
$(OBJ_DIR)/%.o: src/%.c
	@echo + CC $<
	@mkdir -p $(dir $@)
	@$(CC) $(CPPFLAGS) $(CFLAGS) -c -o $@ $<

$(OBJ_DIR)/%.o: src/%.cpp
	@echo + CXX $<
	@mkdir -p $(dir $@)
	@$(CXX) $(CPPFLAGS) $(CXXFLAGS) -c -o $@ $<


# Depencies
-include $(OBJS:.o=.d)

# Some convenient rules

.PHONY: app clean
app: $(BINARY)

$(BINARY): $(OBJS)
	@echo + LD $@
	@$(LD) $(LDFLAGS) -o $@ $^ $(shell $(SDL_CONFIG) --libs) $(LDLIBS)

run: app
	$(BINARY)

clean:
	-rm -rf $(BUILD_DIR)
