#include <stdio.h>
#include <string.h>

int main() {
    char buffer[20] = "Hello, World!";
    printf("Original buffer: %s\n", buffer);
    
    // This causes a strcpy parameter overlap error
    // We're copying from buffer+5 to buffer+3, which means the buffers overlap
    strcpy(buffer+3, buffer+5);
    
    printf("Buffer after strcpy: %s\n", buffer);
    
    return 0;
}