#include <stdio.h>

int main() {
    int *ptr = NULL;
    
    {   // New scope begins
        int local_variable = 42;
        ptr = &local_variable;
        // local_variable is valid only within this scope
    }   // Scope ends, local_variable is destroyed
    
    // At this point, local_variable has gone out of scope
    // but ptr still points to its memory location
    
    // This is a stack-use-after-scope vulnerability
    printf("Value: %d\n", *ptr);
    
    return 0;
}