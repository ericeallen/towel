---
name: Feature request
about: Suggest an idea for this project
title: '[FEATURE] '
labels: enhancement
assignees: ''
---

**Is your feature request related to a problem? Please describe.**
A clear and concise description of what the problem is. Ex. I'm always frustrated when [...]

**Describe the solution you'd like**
A clear and concise description of what you want to happen.

**Describe alternatives you've considered**
A clear and concise description of any alternative solutions or features you've considered.

**Code Example**
If applicable, provide a code example showing:
1. Current duplicate code pattern
2. How you'd like Towel to refactor it

```python
# Before (current duplicate code):
def process_a(data):
    # ...

def process_b(data):
    # ...

# After (desired refactoring):
def _helper(data, param):
    # ...

def process_a(data):
    return _helper(data, value_a)

def process_b(data):
    return _helper(data, value_b)
```

**Use Case**
Describe the use case or scenario where this feature would be helpful.

**Additional context**
Add any other context, screenshots, or examples about the feature request here.

**Potential Impact**
- [ ] This would help detect more duplicate code
- [ ] This would make refactorings safer
- [ ] This would improve usability
- [ ] This would improve performance
- [ ] Other (please describe)
