# Part 6: Build Your App From Scratch

**Now that your infrastructure is verified and running, let's build your actual app.**

This guide walks you through understanding what you have and building your own features from scratch.

---

## 📋 Table of Contents

1. [Understanding the Current Structure](#understanding-the-current-structure)
2. [Your Verification Dashboard](#your-verification-dashboard)
3. [Key Concepts](#key-concepts)
4. [Step-by-Step: Building a New Feature](#step-by-step-building-a-new-feature)
5. [Database: Adding New Tables](#database-adding-new-tables)
6. [Frontend: Creating Pages and Components](#frontend-creating-pages-and-components)
7. [Styling and Theming](#styling-and-theming)
8. [Authentication and User Data](#authentication-and-user-data)
9. [Deploying Your Changes](#deploying-your-changes)
10. [Common Patterns and Examples](#common-patterns-and-examples)
11. [Best Practices](#best-practices)

---

## Understanding the Current Structure

### What You Have

```
app/
├── components/          # Reusable React components
│   ├── VerificationDashboard.tsx  # Infrastructure verification dashboard
│   ├── DomainVerification.tsx     # Domain configuration check
│   ├── AuthVerification.tsx      # Authentication check
│   ├── DatabaseVerification.tsx  # Database connection check
│   └── EmailRoutingVerification.tsx  # Email routing check
├── auth/               # Authentication pages
│   ├── signin/         # Sign in/sign up page
│   └── signout/        # Sign out route
├── lib/                # Utility functions and clients
│   ├── supabase.ts     # Client-side Supabase client
│   └── supabase-server.ts  # Server-side Supabase client
├── types/              # TypeScript type definitions
│   └── index.ts
├── layout.tsx          # Root layout (wraps all pages)
├── page.tsx            # Home page (shows verification dashboard)
└── globals.css         # Global styles

supabase/
├── migrations/         # Database migrations
│   └── 20240101000001_initial_schema.sql
└── config.toml        # Supabase configuration
```

## Your Verification Dashboard

When you first visit your deployed app, you'll see the **Infrastructure Verification Dashboard**. This dashboard verifies that all your infrastructure components are properly configured:

### ✅ Domain Verification
- Checks that your custom domain is properly configured
- Verifies DNS records are set correctly
- Confirms HTTPS is working

### ✅ Authentication Verification
- Tests Supabase authentication connection
- Verifies sign-in/sign-up functionality
- Confirms user session management

### ✅ Database Verification
- Tests database connection
- Verifies migrations have been applied
- Confirms you can read/write data

### ✅ Email Routing Verification
- Checks email routing configuration (if enabled)
- Verifies forwarding rules are set up

Once all verifications pass, you'll see a success message indicating your infrastructure is ready. **This is your canvas** - a clean, verified foundation where you can start building your application.

---

## Key Concepts

**Next.js App Router:**
- `page.tsx` = a route (e.g., `app/page.tsx` = `/`, `app/about/page.tsx` = `/about`)
- `layout.tsx` = wrapper that applies to all child routes
- `route.ts` = API endpoint (e.g., `app/api/items/route.ts` = `/api/items`)

**Supabase Integration:**
- `lib/supabase.ts` = client-side (browser) - for client components
- `lib/supabase-server.ts` = server-side (server) - for server components and API routes
- Always use server client in server components for security

**TypeScript:**
- Types defined in `app/types/index.ts`
- Supabase types can be generated: `npx supabase gen types typescript --project-id YOUR_ID > types/supabase.ts`

**Working in GitHub:**
- Edit files directly in GitHub's web interface
- Or use GitHub Codespaces for a full development environment
- All changes deploy automatically when you push to `main`

---

## Step-by-Step: Building a New Feature

Let's build a **Notes** feature as an example. This will show you the complete process from database to UI.

### Step 1: Create Database Migration

Create a new migration file:

```bash
# In supabase/migrations/
# Create: 20240102000001_create_notes.sql
```

```sql
-- Create notes table
CREATE TABLE IF NOT EXISTS public.notes (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    title TEXT NOT NULL,
    content TEXT,
    user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Enable Row Level Security
ALTER TABLE public.notes ENABLE ROW LEVEL SECURITY;

-- Create policies
CREATE POLICY "Users can view their own notes" ON public.notes
    FOR SELECT USING (auth.uid() = user_id);

CREATE POLICY "Users can insert their own notes" ON public.notes
    FOR INSERT WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can update their own notes" ON public.notes
    FOR UPDATE USING (auth.uid() = user_id);

CREATE POLICY "Users can delete their own notes" ON public.notes
    FOR DELETE USING (auth.uid() = user_id);

-- Create trigger for updated_at
CREATE TRIGGER handle_notes_updated_at
    BEFORE UPDATE ON public.notes
    FOR EACH ROW
    EXECUTE FUNCTION public.handle_updated_at();

-- Create indexes
CREATE INDEX IF NOT EXISTS idx_notes_user_id ON public.notes(user_id);
CREATE INDEX IF NOT EXISTS idx_notes_created_at ON public.notes(created_at DESC);
```

Apply the migration:

```bash
npx supabase db push
```

### Step 2: Update TypeScript Types

Update `app/types/index.ts`:

```typescript
export interface Note {
  id: string;
  title: string;
  content: string | null;
  user_id: string;
  created_at: string;
  updated_at: string;
}
```

### Step 3: Create Components

Create `app/components/AddNote.tsx`:

```typescript
'use client';

import { useState } from 'react';
import { createClient } from '@/app/lib/supabase';
import { useRouter } from 'next/navigation';

export default function AddNote() {
  const [title, setTitle] = useState('');
  const [content, setContent] = useState('');
  const [loading, setLoading] = useState(false);
  const router = useRouter();
  const supabase = createClient();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);

    const {
      data: { session },
    } = await supabase.auth.getSession();

    if (!session) {
      alert('You must be logged in to create notes');
      setLoading(false);
      return;
    }

    const { error } = await supabase
      .from('notes')
      .insert([
        {
          title,
          content,
          user_id: session.user.id,
        },
      ]);

    if (error) {
      console.error('Error creating note:', error);
      alert('Failed to create note');
    } else {
      setTitle('');
      setContent('');
      router.refresh(); // Refresh to show new note
    }

    setLoading(false);
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <div>
        <label htmlFor="title" className="block text-sm font-medium text-gray-700 mb-1">
          Title
        </label>
        <input
          id="title"
          type="text"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          required
          className="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
          placeholder="Note title"
        />
      </div>
      <div>
        <label htmlFor="content" className="block text-sm font-medium text-gray-700 mb-1">
          Content
        </label>
        <textarea
          id="content"
          value={content}
          onChange={(e) => setContent(e.target.value)}
          rows={4}
          className="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
          placeholder="Note content"
        />
      </div>
      <button
        type="submit"
        disabled={loading}
        className="w-full bg-blue-600 text-white py-2 px-4 rounded-md hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 disabled:opacity-50 disabled:cursor-not-allowed"
      >
        {loading ? 'Creating...' : 'Create Note'}
      </button>
    </form>
  );
}
```

Create `app/components/NotesList.tsx`:

```typescript
'use client';

import { createClient } from '@/app/lib/supabase';
import { useRouter } from 'next/navigation';
import { Note } from '@/app/types';

interface NotesListProps {
  notes: Note[];
}

export default function NotesList({ notes }: NotesListProps) {
  const router = useRouter();
  const supabase = createClient();

  const handleDelete = async (id: string) => {
    if (!confirm('Are you sure you want to delete this note?')) {
      return;
    }

    const { error } = await supabase.from('notes').delete().eq('id', id);

    if (error) {
      console.error('Error deleting note:', error);
      alert('Failed to delete note');
    } else {
      router.refresh();
    }
  };

  if (notes.length === 0) {
    return (
      <div className="text-center py-8 text-gray-500">
        No notes yet. Create your first note above!
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {notes.map((note) => (
        <div
          key={note.id}
          className="bg-white border border-gray-200 rounded-lg p-4 shadow-sm hover:shadow-md transition-shadow"
        >
          <div className="flex justify-between items-start mb-2">
            <h3 className="text-lg font-semibold text-gray-900">{note.title}</h3>
            <button
              onClick={() => handleDelete(note.id)}
              className="text-red-600 hover:text-red-800 text-sm font-medium"
            >
              Delete
            </button>
          </div>
          <p className="text-gray-600 whitespace-pre-wrap">{note.content}</p>
          <p className="text-xs text-gray-400 mt-2">
            {new Date(note.created_at).toLocaleDateString()}
          </p>
        </div>
      ))}
    </div>
  );
}
```

### Step 4: Create a Page

Create `app/notes/page.tsx`:

```typescript
import { createServerClient } from '@/app/lib/supabase-server';
import { redirect } from 'next/navigation';
import AddNote from '@/app/components/AddNote';
import NotesList from '@/app/components/NotesList';

export default async function NotesPage() {
  const supabase = createServerClient();

  const {
    data: { session },
  } = await supabase.auth.getSession();

  if (!session) {
    redirect('/auth/signin');
  }

  // Fetch notes for the current user
  const { data: notes, error } = await supabase
    .from('notes')
    .select('*')
    .eq('user_id', session.user.id)
    .order('created_at', { ascending: false });

  if (error) {
    console.error('Error fetching notes:', error);
  }

  return (
    <div className="max-w-4xl mx-auto py-8 px-4 sm:px-6 lg:px-8">
      <div className="bg-white rounded-lg shadow-sm border p-6">
        <div className="mb-8">
          <h1 className="text-3xl font-bold text-gray-900 mb-2">My Notes</h1>
          <p className="text-gray-600">Create and manage your personal notes.</p>
        </div>

        <div className="space-y-8">
          <div>
            <h2 className="text-xl font-semibold text-gray-900 mb-4">New Note</h2>
            <AddNote />
          </div>

          <div>
            <h2 className="text-xl font-semibold text-gray-900 mb-4">Your Notes</h2>
            <NotesList notes={notes || []} />
          </div>
        </div>
      </div>
    </div>
  );
}
```

### Step 5: Add Navigation

Update `app/layout.tsx` to add a link to the notes page:

```typescript
// ... existing imports ...

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className={inter.className}>
        <nav className="bg-white border-b border-gray-200">
          <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
            <div className="flex justify-between h-16">
              <div className="flex space-x-8">
                <Link
                  href="/"
                  className="inline-flex items-center px-1 pt-1 text-sm font-medium text-gray-900"
                >
                  Home
                </Link>
                <Link
                  href="/notes"
                  className="inline-flex items-center px-1 pt-1 text-sm font-medium text-gray-600 hover:text-gray-900"
                >
                  Notes
                </Link>
              </div>
              {/* ... existing sign out link ... */}
            </div>
          </div>
        </nav>
        {children}
      </body>
    </html>
  );
}
```

### Step 6: Test Your Feature

1. Start dev server: `npm run dev`
2. Sign in to your app
3. Navigate to `/notes`
4. Create a note
5. Verify it appears in the list
6. Delete a note and verify it's removed

---

## Database: Adding New Tables

### Migration Best Practices

1. **Always use migrations** - Never modify the database directly
2. **Use descriptive names** - `20240102000001_create_notes.sql`
3. **Include RLS policies** - Always enable Row Level Security
4. **Add indexes** - For foreign keys and commonly queried columns
5. **Use IF NOT EXISTS** - Makes migrations idempotent

### Example: Adding a Relationship

Let's add tags to notes:

```sql
-- Create tags table
CREATE TABLE IF NOT EXISTS public.tags (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    color TEXT,
    user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Create junction table for notes and tags (many-to-many)
CREATE TABLE IF NOT EXISTS public.note_tags (
    note_id UUID REFERENCES public.notes(id) ON DELETE CASCADE,
    tag_id UUID REFERENCES public.tags(id) ON DELETE CASCADE,
    PRIMARY KEY (note_id, tag_id)
);

-- RLS for tags
ALTER TABLE public.tags ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can view their own tags" ON public.tags
    FOR SELECT USING (auth.uid() = user_id);

CREATE POLICY "Users can manage their own tags" ON public.tags
    FOR ALL USING (auth.uid() = user_id);

-- RLS for note_tags (users can only link their own notes/tags)
ALTER TABLE public.note_tags ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can manage their own note_tags" ON public.note_tags
    FOR ALL USING (
        EXISTS (
            SELECT 1 FROM public.notes
            WHERE notes.id = note_tags.note_id
            AND notes.user_id = auth.uid()
        )
        AND EXISTS (
            SELECT 1 FROM public.tags
            WHERE tags.id = note_tags.tag_id
            AND tags.user_id = auth.uid()
        )
    );
```

---

## Frontend: Creating Pages and Components

### Page Types

**Server Component (Default):**
```typescript
// app/page.tsx
import { createServerClient } from '@/app/lib/supabase-server';

export default async function HomePage() {
  const supabase = createServerClient();
  // Fetch data here
  const { data } = await supabase.from('items').select('*');
  
  return <div>{/* Render data */}</div>;
}
```

**Client Component:**
```typescript
// app/components/InteractiveComponent.tsx
'use client';

import { useState } from 'react';

export default function InteractiveComponent() {
  const [count, setCount] = useState(0);
  
  return (
    <button onClick={() => setCount(count + 1)}>
      Count: {count}
    </button>
  );
}
```

**When to use which:**
- **Server Components**: Default, use for data fetching, better performance
- **Client Components**: Use for interactivity (onClick, useState, useEffect, forms)

### Component Structure

```
components/
├── ui/              # Reusable UI elements (buttons, inputs, cards)
├── features/        # Feature-specific components
│   ├── notes/
│   │   ├── AddNote.tsx
│   │   ├── NotesList.tsx
│   │   └── NoteCard.tsx
│   └── your-feature/
└── layout/          # Layout components (Header, Footer, Sidebar)
```

---

## Styling and Theming

### Tailwind CSS

The app uses Tailwind CSS for styling. Examples:

```typescript
// Card
<div className="bg-white rounded-lg shadow-sm border p-6">

// Button
<button className="bg-blue-600 text-white py-2 px-4 rounded-md hover:bg-blue-700">

// Input
<input className="w-full px-3 py-2 border border-gray-300 rounded-md" />

// Responsive
<div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
```

### Custom Colors

Add to `tailwind.config.js`:

```javascript
module.exports = {
  theme: {
    extend: {
      colors: {
        primary: {
          50: '#eff6ff',
          500: '#3b82f6',
          600: '#2563eb',
          700: '#1d4ed8',
        },
      },
    },
  },
};
```

Use: `className="bg-primary-500"`

### Dark Mode

Add dark mode support:

```typescript
// app/layout.tsx
<html lang="en" className="dark">
```

Configure Tailwind for dark mode, then use:
```typescript
className="bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
```

---

## Authentication and User Data

### Getting Current User

**Server Component:**
```typescript
const supabase = createServerClient();
const { data: { session } } = await supabase.auth.getSession();
const user = session?.user;
```

**Client Component:**
```typescript
const supabase = createClient();
const { data: { session } } = await supabase.auth.getSession();
const user = session?.user;
```

### Protecting Routes

```typescript
// app/protected/page.tsx
import { createServerClient } from '@/app/lib/supabase-server';
import { redirect } from 'next/navigation';

export default async function ProtectedPage() {
  const supabase = createServerClient();
  const { data: { session } } = await supabase.auth.getSession();
  
  if (!session) {
    redirect('/auth/signin');
  }
  
  return <div>Protected content</div>;
}
```

### User Profile Data

Create a profiles table:

```sql
CREATE TABLE IF NOT EXISTS public.profiles (
    id UUID REFERENCES auth.users(id) ON DELETE CASCADE PRIMARY KEY,
    username TEXT UNIQUE,
    avatar_url TEXT,
    bio TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
```

---

## Deploying Your Changes

### Workflow

1. **Make changes in GitHub**
   - Edit files directly in the web interface
   - Or use GitHub Codespaces for a full editor
2. **Commit changes:**
   - Use GitHub's commit interface
   - Or push from Codespaces: `git add . && git commit -m "Add notes feature" && git push`
3. **Automatic deployment:**
   - Vercel builds and deploys automatically
   - GitHub Actions runs database migrations
   - Your changes are live in ~2-3 minutes

### Database Migrations

Migrations run automatically via GitHub Actions on push to `main`. You can also trigger the workflow manually in GitHub Actions.

### Environment Variables

If you add new environment variables:

1. Add to `.env.local` for local development
2. Add to Vercel Dashboard → Settings → Environment Variables
3. Or add to `terraform/modules/vercel/main.tf` for automatic management

---

## Common Patterns and Examples

### Pattern 1: CRUD Operations

**Create:**
```typescript
const { error } = await supabase
  .from('items')
  .insert([{ title: 'New Item', user_id: user.id }]);
```

**Read:**
```typescript
const { data, error } = await supabase
  .from('items')
  .select('*')
  .eq('user_id', user.id);
```

**Update:**
```typescript
const { error } = await supabase
  .from('items')
  .update({ title: 'Updated' })
  .eq('id', itemId);
```

**Delete:**
```typescript
const { error } = await supabase
  .from('items')
  .delete()
  .eq('id', itemId);
```

### Pattern 2: Optimistic Updates

Update UI immediately, then sync with database:

```typescript
const [items, setItems] = useState(initialItems);

const handleDelete = async (id: string) => {
  // Optimistic update
  setItems(items.filter(item => item.id !== id));
  
  // Sync with database
  const { error } = await supabase
    .from('items')
    .delete()
    .eq('id', id);
  
  if (error) {
    // Rollback on error
    setItems(initialItems);
    alert('Failed to delete');
  }
};
```

### Pattern 3: Real-time Updates

Subscribe to database changes:

```typescript
'use client';

useEffect(() => {
  const channel = supabase
    .channel('items-changes')
    .on(
      'postgres_changes',
      {
        event: '*',
        schema: 'public',
        table: 'items',
        filter: `user_id=eq.${user.id}`,
      },
      (payload) => {
        console.log('Change received!', payload);
        // Update your state
      }
    )
    .subscribe();

  return () => {
    supabase.removeChannel(channel);
  };
}, []);
```

### Pattern 4: Loading States

```typescript
const [loading, setLoading] = useState(false);

const handleSubmit = async () => {
  setLoading(true);
  try {
    // ... operation
  } finally {
    setLoading(false);
  }
};

return (
  <button disabled={loading}>
    {loading ? 'Loading...' : 'Submit'}
  </button>
);
```

### Pattern 5: Error Handling

```typescript
const handleOperation = async () => {
  try {
    const { data, error } = await supabase.from('items').select('*');
    
    if (error) throw error;
    
    // Use data
  } catch (error) {
    console.error('Error:', error);
    // Show user-friendly message
    alert('Something went wrong. Please try again.');
  }
};
```

---

## Best Practices

### 1. Security

- ✅ Always use Row Level Security (RLS) on tables
- ✅ Use server-side Supabase client in server components
- ✅ Never expose service role key in client code
- ✅ Validate user input
- ✅ Use parameterized queries (Supabase handles this)

### 2. Performance

- ✅ Fetch data in server components when possible
- ✅ Use indexes on frequently queried columns
- ✅ Implement pagination for large datasets
- ✅ Use `router.refresh()` instead of full page reloads
- ✅ Optimize images (use Next.js Image component)

### 3. Code Organization

- ✅ Keep components small and focused
- ✅ Extract reusable logic into custom hooks
- ✅ Use TypeScript for type safety
- ✅ Group related components in feature folders
- ✅ Keep migrations organized and descriptive

### 4. User Experience

- ✅ Show loading states during async operations
- ✅ Provide feedback on errors
- ✅ Use optimistic updates for better perceived performance
- ✅ Make forms accessible (labels, ARIA attributes)
- ✅ Test on mobile devices

### 5. Deployment

- ✅ Test locally before pushing
- ✅ Keep migrations backward-compatible when possible
- ✅ Monitor Vercel deployments for errors
- ✅ Check GitHub Actions for migration failures
- ✅ Have a rollback plan for migrations

---

## Your Canvas is Ready

Once all verifications pass on your dashboard, you have a clean canvas to build on:

- ✅ **Infrastructure verified** - Domain, Auth, Database, Email all working
- ✅ **Authentication ready** - Users can sign up and sign in
- ✅ **Database connected** - Ready for your data models
- ✅ **Deployment automated** - Every push deploys automatically

### Next Steps

1. **Plan your app** - What problem are you solving?
2. **Design your data model** - What tables do you need?
3. **Build your first feature** - Start with core functionality
4. **Iterate and improve** - Add features incrementally

The verification dashboard will remain accessible, but you can now replace `app/page.tsx` with your own homepage and start building your application features.

---

## Resources

- [Next.js Documentation](https://nextjs.org/docs)
- [Supabase Documentation](https://supabase.com/docs)
- [Tailwind CSS Documentation](https://tailwindcss.com/docs)
- [TypeScript Handbook](https://www.typescriptlang.org/docs/)

---

**You're ready to build! Start with one feature, test it, then add more. Good luck! 🚀**

