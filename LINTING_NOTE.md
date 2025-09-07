# Linting Note

## Expected Linting Errors

You may see TypeScript and linting errors in your IDE when first opening this template. This is **completely normal** and expected behavior for template repositories.

### Why This Happens

- The template doesn't include `node_modules/` (which is correct for templates)
- TypeScript and ESLint need the installed dependencies to resolve types
- React, Next.js, and other module types are not available until `npm install` is run

### How to Fix

Simply run:

```bash
npm install
```

This will:

- Install all dependencies from `package.json`
- Download TypeScript type definitions
- Resolve all module imports
- Clear all linting errors

### After Installation

Once dependencies are installed, you should see:

- ✅ No TypeScript errors
- ✅ No ESLint errors
- ✅ Proper IntelliSense and autocomplete
- ✅ Working development server

### Template Design

This is intentional template design:

- Keeps the repository lightweight
- Prevents committing `node_modules/`
- Forces users to install dependencies (ensuring compatibility)
- Follows best practices for template repositories

## Quick Start

```bash
# Clone the template
git clone <your-template-repo>
cd <your-project>

# Install dependencies (fixes all linting errors)
npm install

# Start development
npm run dev
```

That's it! All linting errors will be resolved after running `npm install`.
