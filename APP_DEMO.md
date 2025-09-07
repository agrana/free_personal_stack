# Next.js App Demo

This document explains the included Next.js application and how it demonstrates the key features of the template.

## 🎯 What This Demo Shows

The included Next.js app is a minimal but complete example that demonstrates:

- **Supabase Authentication** - User sign up/sign in
- **Database Operations** - CRUD operations with todos
- **Row Level Security** - Users can only see their own data
- **Real-time Updates** - Live data synchronization
- **TypeScript** - Type-safe development
- **Modern UI** - Clean, responsive design with Tailwind CSS

## 🏗️ App Structure

```
app/
├── components/
│   ├── AddTodo.tsx      # Form to add new todos
│   └── TodoList.tsx     # List and manage todos
├── auth/
│   ├── signin/
│   │   └── page.tsx     # Sign in/sign up page
│   └── signout/
│       └── route.ts     # Sign out API route
├── lib/
│   └── supabase.ts      # Supabase client configuration
├── types/
│   └── index.ts         # TypeScript type definitions
├── layout.tsx           # Root layout with navigation
├── page.tsx             # Home page with todo list
└── globals.css          # Global styles
```

## 🗄️ Database Schema

The app uses a simple `todos` table:

```sql
CREATE TABLE todos (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title TEXT NOT NULL,
    completed BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE
);
```

### Row Level Security (RLS)

The table has RLS enabled with policies that ensure:

- Users can only view their own todos
- Users can only create todos for themselves
- Users can only update their own todos
- Users can only delete their own todos

## 🔐 Authentication Flow

1. **Sign Up**: Users can create new accounts with email/password
2. **Sign In**: Existing users can sign in
3. **Session Management**: Supabase handles session persistence
4. **Protected Routes**: Home page redirects to sign in if not authenticated
5. **Sign Out**: Users can sign out and return to sign in page

## 📱 User Interface

### Home Page (`/`)

- **Navigation Bar**: Shows user email and sign out button
- **Welcome Section**: Explains what the demo shows
- **Todo List**: Interactive list of user's todos
- **Add Todo Form**: Input field to create new todos

### Sign In Page (`/auth/signin`)

- **Email/Password Form**: For authentication
- **Sign Up Option**: Create new accounts
- **Demo Instructions**: Helpful text for testing

## 🚀 Key Features Demonstrated

### 1. Supabase Integration

- Client-side and server-side Supabase clients
- Type-safe database operations
- Real-time subscriptions (ready to implement)

### 2. Next.js App Router

- Modern file-based routing
- Server and client components
- API routes for authentication

### 3. TypeScript

- Type-safe database operations
- Interface definitions for data models
- Compile-time error checking

### 4. Tailwind CSS

- Utility-first styling
- Responsive design
- Modern UI components

### 5. Database Operations

- **Create**: Add new todos
- **Read**: Fetch user's todos
- **Update**: Mark todos as complete/incomplete
- **Delete**: Remove todos

## 🧪 Testing the Demo

1. **Start the app**: `npm run dev`
2. **Visit**: http://localhost:3000
3. **Sign up**: Create a new account
4. **Add todos**: Use the form to create todos
5. **Toggle completion**: Click the checkbox to mark complete
6. **Delete todos**: Click the trash icon to remove
7. **Sign out**: Test the sign out functionality

## 🔧 Customization Ideas

This demo can be extended to show:

- **Real-time Updates**: Add Supabase real-time subscriptions
- **File Uploads**: Use Supabase Storage for file uploads
- **Advanced Auth**: Add social login providers
- **More Tables**: Add categories, tags, or user profiles
- **API Routes**: Create custom API endpoints
- **Middleware**: Add authentication middleware
- **Error Handling**: Implement proper error boundaries
- **Loading States**: Add skeleton loaders and spinners

## 📚 Learning Resources

- [Next.js Documentation](https://nextjs.org/docs)
- [Supabase Documentation](https://supabase.com/docs)
- [Tailwind CSS Documentation](https://tailwindcss.com/docs)
- [TypeScript Documentation](https://www.typescriptlang.org/docs)

## 🎉 Next Steps

After exploring this demo:

1. **Customize the UI** - Modify colors, layout, and components
2. **Add Features** - Implement real-time updates, file uploads, etc.
3. **Deploy** - Use the included Vercel configuration
4. **Scale** - Add more tables, relationships, and business logic

This demo provides a solid foundation for building more complex applications with the same stack!
